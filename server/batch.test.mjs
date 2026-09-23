import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {NexusStore} from './storage.mjs';
import {normalizeSources,selectUnprocessed,startBatch} from './batch.mjs';
import {sourceVersions} from '../ui/source-version.js';

test('browser and host agree on versions including role, swipe and stable message id', async()=>{
    const messages=[{mes:'开场',name:'旁白'}, {mes:'同一文字',is_user:true,swipe_id:2,extra:{message_id:'stable'}}];
    const host=normalizeSources(messages);
    assert.deepEqual(await sourceVersions(messages),host.map(m=>m.extra.nexus_source_version));
    const fallback=await sourceVersions(messages,null);
    const versions=new Map();
    NexusStore.prototype.setSourceVersions.call({sourceVersions:versions},'lan',fallback);
    assert.deepEqual(versions.get('lan'),host.map(m=>m.extra.nexus_source_version));
    const changed=normalizeSources([{...messages[0],swipe_id:1},messages[1]]);
    assert.notEqual(changed[0].extra.nexus_source_version,host[0].extra.nexus_source_version);
});

test('overlap is skipped but source edits, deletion and swipes require review',()=>{
    const messages=[{mes:'开场'},{mes:'提问',is_user:true},{mes:'回答'}];
    const all=normalizeSources(messages);
    const coverage=[{sources:[{floor:0,id:all[0].extra.nexus_source_id,version:all[0].extra.nexus_source_version}]}];
    assert.deepEqual(selectUnprocessed(all,coverage,0,2).map(m=>m.extra.nexus_source_floor),[1,2]);
    for(const altered of [[{mes:'编辑'},...messages.slice(1)],messages.slice(1),[{...messages[0],swipe_id:1},...messages.slice(1)]]) assert.throws(()=>selectUnprocessed(normalizeSources(altered),coverage,0,2),/来源/);
});

for (const editDuringRun of [false,true]) test(`host executes a fixture result with source conflict=${editDuringRun}, without launching a model`,()=>{
    const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'nexus-host-fixture-'));
    const store=new NexusStore({dataRoot,entityTypes:['event','item']});
    try {
        store.bindChat({chatId:'fixture',messageCount:1});
        store.saveApiPreset({name:'fixture',baseUrl:'https://unused.invalid/v1',apiKey:'fixture-only-secret',model:'fixture-model',isPrimary:true});
        const job=store.createBatchJob({chatId:'fixture',startFloor:0,endFloor:0,entityTypes:['event','item']});
        let child, outputPath, argsSeen;
        const fakeSpawn=(_binary,args)=>{
            argsSeen=args;outputPath=args[args.indexOf('--result-json')+1];
            child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();return child;
        };
        startBatch({store,chatId:'fixture',job,messages:[{mes:'开场中有一柄旧剑。'}],spawnProcess:fakeSpawn});
        assert.equal(store.getBatchJob('fixture',job.id).status,'running');
        assert.ok(argsSeen.includes('--snapshot'));assert.ok(argsSeen.includes('--profiles'));
        assert.equal(argsSeen.some(arg=>arg.includes('fixture-only-secret')),false);
        if(editDuringRun) store.setSourceVersions('fixture',['edited']);
        fs.writeFileSync(outputPath,JSON.stringify({status:'completed',validation:{valid:true},state:{id_maps:{entity:{'item:旧剑':'sword'}}},entities:[{id:'sword',type:'item',description:'旧剑',components:{}}],diagnostics:[]}));
        child.emit('close',0);
        assert.equal(store.getBatchJob('fixture',job.id).status,editDuringRun?'failed':'completed');
        assert.equal(store.listEntities({chatId:'fixture'}).length,editDuringRun?0:1);
        assert.equal(store.sourceCoverage('fixture').length,editDuringRun?0:1);
    } finally {store.close();fs.rmSync(dataRoot,{recursive:true,force:true});}
});
