"""Offline browser fixture. Reads only src/static; no server, private data or network."""
from pathlib import Path
import argparse, os, re, shutil
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('repo', type=Path, help='Repository root (only src/static is read)')
parser.add_argument('mode', nargs='?', default='fixed', choices=['original','lifecycle','raw','layout','fixed','combined'])
parser.add_argument('--browser', default=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium') or shutil.which('chromium-browser'), help='Chromium executable; omit to use Playwright browser')
args = parser.parse_args()
ROOT = args.repo.resolve()
STATIC = ROOT / 'src/static'
MODE = args.mode

def sources():
    result = {}
    def collect(rel):
        if rel in result: return
        source = (STATIC/rel).read_text(encoding='utf-8')
        result[rel] = source
        for spec in re.findall(r"\bfrom\s*['\"](\.[^'\"]+)['\"]",source):
            collect((STATIC/Path(rel).parent/spec).resolve().relative_to(STATIC.resolve()).as_posix())
    collect('app.js')
    return result

def css():
    sheet=(STATIC/'styles.css').read_text(encoding='utf-8')
    return re.sub(r'@import\s+url\([\'\"]?([^\'\"\)]+)[\'\"]?\)\s*;',lambda m:(STATIC/m[1]).read_text(encoding='utf-8'),sheet)

HTML=re.sub(r'<script\b[^>]*>.*?</script>', '', (STATIC/'index.html').read_text(encoding='utf-8'), flags=re.S|re.I)
HTML=re.sub(r'<link\b[^>]*>', '', HTML, flags=re.I).replace('</head>', '<style>'+css()+'</style></head>')
INIT=r'''({modules, selected, seedCounts}) => {
 const storage = new Map(selected ? [['localAgentSessionId', selected]] : []);
 Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)}});
 if (!crypto.randomUUID) Object.defineProperty(crypto,'randomUUID',{value:()=> 'fixture-created'});
 window.marked={parse:s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;')};
 window.hljs={highlightElement(){}};
 const fixture = window.fixture = {
   counts:{...seedCounts}, calls:[], holds:[], pending:[], rawFailOnce:false,
   release(path, fail=false) {const idx=this.pending.findIndex(x=>x.path===path); if(idx<0) throw Error('missing pending '+path); const [x]=this.pending.splice(idx,1); x.resolve(fail?new Response('{}',{status:500}):x.response);},
 };
 function response(obj,status=200){return new Response(JSON.stringify(obj),{status,headers:{'Content-Type':'application/json'}})}
 window.fetch=async (url,opts={})=>{
   const {pathname:path,searchParams}=new URL(url,'https://fixture.invalid'); const method=opts.method||'GET';
   const body=opts.body?JSON.parse(opts.body):null;
   fixture.calls.push({method,path,query:searchParams.toString(),body});
   let r;
   if(path==='/api/health') r=response({});
   else if(path==='/api/sessions'||path==='/api/sessions/search'){
      if(method==='POST'){let id=body.session_id||'fixture-new'; fixture.counts[id]=0; r=response({session_id:id,title:'Fixture new'});}
      else r=response({sessions:Object.entries(fixture.counts).map(([id,n])=>({session_id:id,title:id,tokens_total:n}))});
   } else if(path==='/api/chat'){
      fixture.counts[body.session_id]=2700;
      r=new Response('data: '+JSON.stringify({type:'content_delta',text:'fixture reply'})+'\n\n',{headers:{'Content-Type':'text/event-stream'}});
   } else if(path.startsWith('/api/sessions/')){
      const [, , , id, ...rest]=path.split('/'); const type=rest.join('/');
      if(method==='DELETE'){delete fixture.counts[id]; r=response({});}
      else if(!Object.hasOwn(fixture.counts,id))r=response({},404);
      else if(type==='messages')r=response({messages:[]});
      else if(type==='context'){
         const used=fixture.counts[id]; r=response({session_id:id,used_tokens:used,context_limit_tokens:64000,percentage:used/640,used_source:'provider_based',breakdown:{other:used,available:64000-used},loaded_files:[],system_prompt:{text:'fixture '+id}});
      } else if(type==='context/messages'){
        if(fixture.rawFailOnce){fixture.rawFailOnce=false;r=response({},500);}
        else {
          const offset=Number(searchParams.get('offset')), limit=Number(searchParams.get('limit')), total=id==='A'?23:0;
          const messages=Array.from({length:Math.max(0,Math.min(limit,total-offset))},(_,i)=>({seq:offset+i,role:'user',raw:{role:'user',content:'fixture '+id+' <img src=x onerror=alert(1)>'}}));
          r=response({session_id:id,offset,limit,total,has_more:offset+messages.length<total,messages});
        }
      } else r=response({},404);
   } else r=response({},404);
   if(fixture.holds.includes(path)) return new Promise(resolve=>fixture.pending.push({path,response:r,resolve}));
   return r;
 };
 const urls = new Map();
 const normalize=(base,spec)=>{let result=base.split('/').slice(0,-1); for(const p of spec.split('/')){if(p==='..') result.pop();else if(p!=='.')result.push(p)} return result.join('/')};
 function makeModule(name){
    if(urls.has(name))return urls.get(name);
    let source=modules[name].replace(/(\bfrom\s*['"])(\.[^'"]+)(['"])/g,(_,a,spec,b)=>a+makeModule(normalize(name,spec))+b);
    const url=URL.createObjectURL(new Blob([source+'\n//# sourceURL=fixture/'+name],{type:'text/javascript'}));urls.set(name,url);return url;
 }
 window.loadFixtureModule=name=>import(makeModule(name));
}'''

def boot(browser,selected='A',seed_counts=None,viewport=None):
    page=browser.new_page(viewport=viewport or {'width':1440,'height':900},reduced_motion='reduce')
    page.set_default_timeout(3500)
    errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
    # No actual HTTP or local files requested by browser.
    page.route('**/*',lambda r:r.abort())
    page.set_content(HTML,wait_until='domcontentloaded')
    page.evaluate(INIT,{'modules':sources(),'selected':selected,'seedCounts':seed_counts if seed_counts is not None else {'A':1600,'B':3400}})
    page.evaluate("loadFixtureModule('app.js')")
    page.wait_for_function("document.querySelector('#connectionBadge').textContent==='UI ready'")
    page.wait_for_selector('.session-item' if (seed_counts is None or seed_counts) else '.session-list-empty')
    return page,errors

def wait_count(page,s): page.wait_for_function('(s)=>document.querySelector("#contextCount").textContent===s',arg=s)
def wait_raw(page,s): page.wait_for_function('(s)=>document.querySelector(".context-detail-raw-status")?.textContent===s',arg=s)
def requests(page,substring): return page.evaluate('(s)=>fixture.calls.filter(x=>x.path.includes(s))',substring)
def holds(page,paths): page.evaluate('(p)=>fixture.holds=p',paths)
def wait_pending(page,path): page.wait_for_function('(p)=>fixture.pending.some(x=>x.path===p)',arg=path)
def release(page,path,fail=False): page.evaluate('([p,f])=>fixture.release(p,f)',[path,fail])
def delete_session(page,sid):
    page.locator(f'.session-item[data-session-id={sid}] .session-delete-btn').click()
    page.locator('.confirm-dialog-button.delete').click()

def check_original(browser):
    page,errors=boot(browser)
    panel=page.locator('#contextPanel'); ring=page.locator('#contextIndicator'); count=page.locator('#contextCount')
    page.wait_for_timeout(50)
    print('original startup:',count.inner_text(),'visible:',panel.is_visible(),'context requests:',requests(page,'/context'))
    ring.hover(); print('original tooltip:',page.locator('.context-tooltip').bounding_box()); ring.click(); print('original ring click opens:',panel.is_visible())
    page.locator('.session-item[data-session-id=B]').click(); wait_count(page,'3.4k');
    print('original selection auto-opens:',panel.is_visible(),'raw status:',repr(page.locator('.context-detail-raw-status').inner_text()),'raw requests:',requests(page,'/context/messages'))
    page.set_viewport_size({'width':1000,'height':800}); print('original tablet columns:',page.locator('.layout').evaluate('(e)=>getComputedStyle(e).gridTemplateColumns'))
    page.set_viewport_size({'width':1440,'height':900}); delete_session(page,'B'); page.wait_for_function("fixture.calls.some(x=>x.path==='/api/sessions/null/context')"); print('original delete:',count.inner_text(),requests(page,'/null/'))
    print('original page errors:',errors); page.close()

def check_lifecycle(browser):
    page,errors=boot(browser)
    panel=page.locator('#contextPanel'); ring=page.locator('#contextIndicator'); count=page.locator('#contextCount')
    wait_count(page,'1.6k'); assert not panel.is_visible(); assert len(requests(page,'/A/context'))==1; assert not requests(page,'/context/messages')
    ring.click(); assert panel.is_visible(); assert ring.get_attribute('aria-expanded')=='true'; assert '1,600 / 64,000 tokens' in page.locator('#contextPanelBody').inner_text()
    ring.click(); assert not panel.is_visible(); assert ring.get_attribute('aria-expanded')=='false'
    ring.focus(); ring.press('Enter'); assert panel.is_visible(); page.keyboard.press('Escape'); assert not panel.is_visible()
    ring.click(); page.locator('#contextClose').click(); assert not panel.is_visible()
    # Leave panel closed while switching. A's pending response cannot overwrite B.
    holds(page,['/api/sessions/B/context']); page.locator('.session-item[data-session-id=B]').click(); wait_pending(page,'/api/sessions/B/context'); wait_count(page,'0'); assert not panel.is_visible()
    page.locator('.session-item[data-session-id=A]').click(); wait_count(page,'1.6k'); release(page,'/api/sessions/B/context'); page.wait_for_timeout(50); assert count.inner_text()=='1.6k' and not panel.is_visible()
    # Guard late error separately while open. Preserve open state on switch.
    ring.click(); holds(page,['/api/sessions/B/context']); page.locator('.session-item[data-session-id=B]').click(); wait_pending(page,'/api/sessions/B/context'); wait_count(page,'0'); assert panel.is_visible() and 'Loading context' in page.locator('#contextPanelBody').inner_text()
    page.locator('.session-item[data-session-id=A]').click(); wait_count(page,'1.6k'); release(page,'/api/sessions/B/context',True); page.wait_for_timeout(50); assert '1,600 / 64,000 tokens' in page.locator('#contextPanelBody').inner_text()
    # Non-stale request error has a functioning retry; non-active deletion must not clear.
    page.locator('.session-item[data-session-id=B]').click(); wait_pending(page,'/api/sessions/B/context'); release(page,'/api/sessions/B/context',True)
    page.wait_for_function("document.querySelector('#contextPanelBody').textContent.includes('Unable to load context')")
    holds(page,[]); page.locator('#contextPanelBody .context-retry').click(); wait_count(page,'3.4k')
    page.locator('.session-item[data-session-id=A]').click(); wait_count(page,'1.6k')
    # Delete active B with its context pending, then deliver old B snapshot.
    holds(page,['/api/sessions/B/context'])
    page.locator('.session-item[data-session-id=B]').click(); wait_pending(page,'/api/sessions/B/context'); delete_session(page,'B'); page.wait_for_function("localStorage.getItem('localAgentSessionId')===null"); wait_count(page,'0'); assert 'No context available' in page.locator('#contextPanelBody').inner_text(); release(page,'/api/sessions/B/context'); page.wait_for_timeout(50); assert count.inner_text()=='0'; assert not requests(page,'/null/')
    # Non-null normal selection, active deletion after resolved usage, first implicit send.
    holds(page,[]); page.locator('.session-item[data-session-id=A]').click(); wait_count(page,'1.6k'); delete_session(page,'A'); page.wait_for_function("localStorage.getItem('localAgentSessionId')===null"); wait_count(page,'0'); assert not requests(page,'/null/')
    page.locator('#prompt').fill('fixture send'); page.locator('#sendButton').click(); wait_count(page,'2.7k'); assert panel.is_visible(); assert '2,700 / 64,000 tokens' in page.locator('#contextPanelBody').inner_text()
    selected=page.evaluate("localStorage.getItem('localAgentSessionId')"); counts=page.evaluate('fixture.counts'); assert selected and selected!='A' and selected!='B'
    assert len(requests(page,f'/{selected}/context'))>=1; assert not requests(page,'/null/')
    # Fresh document with identical persisted state, equivalent startup code path to reload.
    page2,errors2=boot(browser,selected,{**counts,'C':800}); wait_count(page2,'2.7k'); assert not page2.locator('#contextPanel').is_visible(); assert not requests(page2,'/context/messages')
    delete_session(page2,'C'); page2.wait_for_function("!document.querySelector('.session-item[data-session-id=C]')"); wait_count(page2,'2.7k'); assert not page2.locator('#contextPanel').is_visible()
    # Normal New chat creates a persisted empty session without opening the panel.
    page2.on('dialog',lambda d:d.accept()); page2.locator('#clearButton').click()
    page2.wait_for_function("localStorage.getItem('localAgentSessionId')==='fixture-new'")
    wait_count(page2,'0'); assert not page2.locator('#contextPanel').is_visible()
    delete_session(page2,selected); page2.wait_for_function("!document.querySelector('.session-item[data-session-id=fixture-created]')")
    assert page2.evaluate("localStorage.getItem('localAgentSessionId')")== 'fixture-new'; assert not page2.locator('#contextPanel').is_visible(); page2.close()
    # A new draft has a UUID but no row; 404 is unavailable, not a null fetch or creation.
    page3,errors3=boot(browser,None,{})
    page3.wait_for_function("document.querySelector('#contextPanelBody').textContent.includes('No context available')")
    wait_count(page3,'0'); assert not page3.locator('#contextPanel').is_visible(); assert not requests(page3,'/null/')
    page3.locator('#contextIndicator').click(); assert not requests(page3,'/context/messages')
    page3.locator('#prompt').fill('fixture draft send'); page3.locator('#sendButton').click(); wait_count(page3,'2.7k'); page3.close()
    assert not errors3,errors3
    # Mobile backdrop works independent of raw or tooltip corrections.
    page.locator('#contextClose').click(); page.set_viewport_size({'width':1000,'height':800}); ring.click(); assert panel.is_visible(); page.locator('#contextBackdrop').click(position={'x':10,'y':100}); assert not panel.is_visible()
    assert not errors and not errors2, (errors,errors2)
    page.close(); print('lifecycle: startup, click/keyboard/Escape/close/backdrop, closed/open switch, late success/error, delete+late success, delete+implicit send, restored fresh document, new chat, inactive delete, fresh draft, context retry: PASS')

def check_raw(browser):
    page,errors=boot(browser); wait_count(page,'1.6k'); assert not requests(page,'/context/messages')
    ring=page.locator('#contextIndicator'); panel=page.locator('#contextPanel'); ring.click(); wait_raw(page,'Showing 20 of 23'); assert page.locator('.context-detail-raw-row').count()==20; assert not page.locator('.context-detail-raw-retry').is_visible()
    assert page.locator('#contextRawSection .context-detail-heading').text_content()=='Raw messages'
    row=page.locator('.context-detail-raw-row').first; assert row.locator('code').inner_text()==''
    row.locator('summary').click(); page.wait_for_function('document.querySelector(".context-detail-raw-row code").textContent.includes("<img")'); assert row.locator('img').count()==0
    page.locator('.context-detail-raw-row').nth(1).locator('summary').click(); assert row.evaluate('(e)=>e.open')
    page.locator('.context-detail-raw-loadmore').click(); wait_raw(page,'Showing 23 of 23'); assert page.locator('.context-detail-raw-row').count()==23; assert not page.locator('.context-detail-raw-loadmore').is_visible()
    # Change active session while a raw request is pending, then resolve it after new one.
    ring.click(); holds(page,['/api/sessions/A/context/messages']); ring.click(); wait_pending(page,'/api/sessions/A/context/messages'); page.locator('.session-item[data-session-id=B]').click(); wait_raw(page,'No stored messages · Showing 0 of 0'); release(page,'/api/sessions/A/context/messages'); page.wait_for_timeout(50); assert page.locator('.context-detail-raw-row').count()==0
    # Close while B raw request pending; stale error must not appear on reopening A.
    ring.click(); holds(page,['/api/sessions/B/context/messages']); ring.click(); wait_pending(page,'/api/sessions/B/context/messages'); ring.click(); page.locator('.session-item[data-session-id=A]').click(); wait_count(page,'1.6k'); holds(page,[]); ring.click(); wait_raw(page,'Showing 20 of 23'); release(page,'/api/sessions/B/context/messages',True); page.wait_for_timeout(50); wait_raw(page,'Showing 20 of 23')
    ring.click(); page.evaluate('fixture.rawFailOnce=true'); ring.click(); page.wait_for_function('document.querySelector(".context-detail-raw-status")?.textContent.includes("Unable to load")'); page.locator('.context-detail-raw-retry').click(); wait_raw(page,'Showing 20 of 23')
    # A same-session stream completion refresh must retire old raw work before
    # the new snapshot returns, not just on an eventual render.
    ring.click(); holds(page,['/api/sessions/A/context/messages','/api/sessions/A/context']); ring.click(); wait_pending(page,'/api/sessions/A/context/messages')
    page.locator('#prompt').fill('fixture refresh'); page.locator('#sendButton').click(); wait_pending(page,'/api/sessions/A/context')
    assert 'Loading context' in page.locator('#contextPanelBody').inner_text()
    release(page,'/api/sessions/A/context/messages',True); page.wait_for_timeout(50); assert 'Loading context' in page.locator('#contextPanelBody').inner_text()
    holds(page,[]); release(page,'/api/sessions/A/context'); wait_count(page,'2.7k'); wait_raw(page,'Showing 20 of 23')
    # Deferred requests to the real exported inspector: same-instance generations, duplicate load, disposal.
    result=page.evaluate(r'''async()=>{
      const {createRawMessageInspector}=await loadFixtureModule('js/shell/context-raw.js');
      const root=document.createElement('div'); document.body.append(root);
      const pending=[]; let current=true;
      const api={getContextRawMessages:(id,args)=>new Promise((resolve,reject)=>pending.push({args,resolve,reject}))};
      const ins=createRawMessageInspector({api,root,sessionId:'A',isCurrent:()=>current});
      const status=()=>root.querySelector('.context-detail-raw-status').textContent;
      const rows=()=>root.querySelectorAll('.context-detail-raw-row').length;
      const result=[]; const tick=()=>new Promise(r=>setTimeout(r,0));
      const data=(seq,offset=0,has_more=true)=>({session_id:'A',offset,limit:20,total:3,has_more,messages:[{seq,role:'user',raw:{content:String(seq)}}]});
      function check(ok,msg){if(!ok)throw Error(msg);result.push(msg)}
      ins.loadMore(); check(pending.length===0,'no pagination before first page');
      ins.loadFirstPage(); check(pending.length===1,'first page dispatches');
      ins.loadFirstPage(); check(pending.length===2,'restart issues new generation');
      pending[0].resolve(data(99)); await tick(); check(rows()===0&&status()==='Loading…','late success ignored');
      ins.loadMore(); check(pending.length===2,'old finally does not unlock current load');
      pending[1].resolve(data(1)); await tick(); check(rows()===1&&root.textContent.includes('#1'),'current generation rendered');
      ins.loadMore(); ins.loadMore(); check(pending.length===3&&pending[2].args.offset===1,'duplicate page suppressed');
      ins.loadFirstPage(); check(pending.length===4,'reset while page pending');
      pending[2].reject(Error('old page error')); await tick(); check(status()==='Loading…','late rejection ignored');
      ins.loadMore(); check(pending.length===4,'late rejection finally does not unlock');
      pending[3].resolve(data(2)); await tick(); check(rows()===1&&root.textContent.includes('#2'),'new generation correct');
      ins.loadMore(); check(pending.length===5,'page queued'); ins.destroy(); pending[4].resolve(data(3,1,false)); await tick(); check(rows()===0,'destroy suppresses late success');
      ins.loadFirstPage(); check(pending.length===5,'destroy prevents new requests');
      const root2=document.createElement('div'); const ins2=createRawMessageInspector({api,root:root2,sessionId:'A',isCurrent:()=>current});
      ins2.loadFirstPage(); check(pending.length===6,'second inspector dispatch'); current=false; pending[5].reject(Error('wrong session')); await tick(); check(!root2.textContent.includes('wrong session'),'stale session rejects ignored');
      root.remove(); return result;
    }'''); print('raw generation tests:',len(result),'PASS')
    assert not errors,errors; page.close(); print('raw: first page, headings, pagination, lazy inert JSON, independent expansion, empty, stale success/error, same-session refresh, retry, hidden controls: PASS')

def check_layout(browser):
    page,errors=boot(browser); wait_count(page,'1.6k'); ring=page.locator('#contextIndicator'); tooltip=page.locator('.context-tooltip'); ring.hover(); box=tooltip.bounding_box(); print('tooltip bounds:',box); assert box['y']>=0 and box['y']+box['height']<=900; assert tooltip.evaluate('(e)=>getComputedStyle(e).opacity')=='1'
    ring.click(); assert page.locator('.layout').evaluate('(e)=>getComputedStyle(e).gridTemplateColumns').split().__len__()==3
    page.set_viewport_size({'width':1000,'height':800}); assert len(page.locator('.layout').evaluate('(e)=>getComputedStyle(e).gridTemplateColumns').split())==2
    page.set_viewport_size({'width':800,'height':800}); assert len(page.locator('.layout').evaluate('(e)=>getComputedStyle(e).gridTemplateColumns').split())==1
    assert not errors; page.close(); print('layout: desktop 3 columns, tablet 2, mobile 1, tooltip inside viewport: PASS')

def check_combined(browser):
    """Run actual bootstrap, conversation, ring and inspector in one document."""
    page, errors = boot(browser)
    wait_count(page, '1.6k')
    page.locator('#contextIndicator').click()
    wait_raw(page, 'Showing 20 of 23')
    # Extend only this fixture's transport; existing lifecycle gates are unchanged.
    page.evaluate(r'''() => {
      const original = window.fetch;
      const text = '<img src=x onerror=alert(1)> synthetic';
      const readArgs = {path:'fixture.txt'}, writeArgs = {path:'never.txt',content:'x'};
      const tool = (id,name,args) => ({id,type:'function',function:{name,arguments:JSON.stringify(args)}});
      const rows = [
        {role:'user',content:'combined fixture'},
        {role:'assistant',content:'before',reasoning_content:'think',usage:{prompt_tokens:100,completion_tokens:10},
          tool_calls:[tool('r','read_file',readArgs),tool('w','write_file',writeArgs)],
          activity:{version:1,parts:[{kind:'reasoning',chars:5,duration_ms:1000},{kind:'content',chars:6}],
            calls:{r:{requested:true},w:{requested:true,approval:{approval_id:'p',status:'denied'}}}}},
        {role:'tool',tool_call_id:'r',content:text,
          activity:{version:1,ok:true,meta:{lines:1,bytes:text.length}}},
        {role:'tool',tool_call_id:'w',content:'Denied',
          activity:{version:1,ok:false,meta:{}}},
        {role:'assistant',content:'after',activity:{version:1,parts:[{kind:'content',chars:5}],calls:{}}}
      ];
      const aliases = {read_file:'Read file',write_file:'Write file'};
      const start = [
        {type:'status',text:'Model turn 1'},
        {type:'reasoning_delta',text:'think'}, {type:'model_part_end',kind:'reasoning',duration_ms:1000},
        {type:'content_delta',text:'before'}, {type:'model_part_end',kind:'content'},
        {type:'tool_call',tool_call_id:'r',name:'read_file',arguments:readArgs,alias:aliases.read_file},
        {type:'tool_result',tool_call_id:'r',name:'read_file',ok:true,result:text,meta:{lines:1,bytes:text.length},alias:aliases.read_file},
        {type:'tool_call',tool_call_id:'w',name:'write_file',arguments:writeArgs,alias:aliases.write_file},
        {type:'approval_required',approval_id:'p',tool_call_id:'w',name:'write_file',arguments:writeArgs,alias:aliases.write_file},
        {type:'approval_decision',approval_id:'p',tool_call_id:'w',name:'write_file',status:'denied',alias:aliases.write_file},
        {type:'tool_result',tool_call_id:'w',name:'write_file',ok:false,result:'Denied',meta:{},alias:aliases.write_file},
        // Deliberately wrong numeric SSE payload: only the GET can paint the ring.
        {type:'context',tokens:9999,max_tokens:10000,percentage:99.99},
      ];
      const tail = [{type:'status',text:'Model turn 2'},{type:'content_delta',text:'after'},
        {type:'model_part_end',kind:'content'},{type:'context',tokens:9999,max_tokens:10000,percentage:99.99},{type:'done'}];
      const encode = events => new TextEncoder().encode(events.map(e=>'data: '+JSON.stringify(e)+'\n\n').join(''));
      const json = value => new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
      let active=false;
      window.fetch = async (url,opts={}) => {
        const u = new URL(url,'https://fixture.invalid');
        const response = await original(url,opts);
        if (u.pathname==='/api/chat') {
          active=true;
          return new Response(new ReadableStream({start(controller){
            controller.enqueue(encode(start));
            fixture.finishCombined=()=>{controller.enqueue(encode(tail));controller.close()};
          }}),{headers:{'Content-Type':'text/event-stream'}});
        }
        if (!active || !response.ok) return response;
        if (u.pathname==='/api/sessions/A/messages') return json({messages:rows,tool_aliases:aliases});
        if (u.pathname==='/api/sessions/A/context') {
          const data=await response.json();
          return json({...data,session_title:'Fixture name',created_at:'2026-01-01 00:00:00',updated_at:'2026-01-02 00:00:00',message_count:5,assistant_message_count:2,user_message_count:1,
            session_lifetime:{tokens_total:4500,source:'provider'},breakdown_source:'tokenizer_assisted',breakdown_sources:{loaded_files:'tokenized',tool_usage:'mixed'},
            attributed_tokens:3000,attribution_difference:-300,breakdown:{loaded_files:100,tool_usage:200,other:2700,available:61300},
            loaded_files:[{path:'fixture.txt',tokens:100,occurrences:1,source:'tokenized'}],tool_usage_details:[{name:'read_file',tokens:200,call_tokens:150,result_tokens:0,definition_tokens:50,calls:1,source:'mixed'}]});
        }
        if (u.pathname==='/api/sessions/A/context/messages') {
          const offset=Number(u.searchParams.get('offset')), limit=Number(u.searchParams.get('limit'));
          const messages=rows.slice(offset,offset+limit).map((raw,i)=>({seq:offset+i,role:raw.role,raw,tool_name:raw.tool_call_id==='r'?'read_file':raw.tool_call_id==='w'?'write_file':undefined}));
          return json({session_id:'A',offset,limit,total:rows.length,has_more:offset+messages.length<rows.length,messages});
        }
        return response;
      };
    }''')
    holds(page, ['/api/sessions/A/context'])
    page.locator('#prompt').fill('combined fixture')
    page.locator('#sendButton').click()
    wait_pending(page, '/api/sessions/A/context')  # debounced SSE while stream is still open
    wait_count(page, '1.6k')
    assert page.locator('#messages .approval-state').inner_text() == 'Denied'
    assert page.locator('#messages .approval-controls button').count() == 0
    assert page.locator('#messages .group-count').all_text_contents() == ['×1','×1']
    holds(page, [])
    release(page, '/api/sessions/A/context')
    wait_count(page, '2.7k')
    wait_raw(page, 'Showing 5 of 5')
    page.evaluate('fixture.finishCombined()')
    page.wait_for_function("document.querySelector('#sendButton').textContent.includes('Send')")
    wait_raw(page, 'Showing 5 of 5')
    # Final refresh, not the numeric SSE payload, owns the panel and ring.
    wait_count(page, '2.7k')
    page.locator('.context-detail-files:not(.context-detail-tools) summary').click()
    assert page.locator('.context-detail-files:not(.context-detail-tools) .context-detail-files-path').inner_text() == 'fixture.txt'
    assert '4,500' in page.locator('.context-detail-lifetime').inner_text()
    assert 'Fixture name' in page.locator('.context-detail-session-info').inner_text()
    assert 'Tokenizer-assisted' in page.locator('.context-detail-source').inner_text()
    assert '300 more' in page.locator('.context-detail-difference').inner_text()
    page.locator('.context-detail-tools summary').click()
    assert 'read_file' in page.locator('.context-detail-tool-row').inner_text()
    assert 'calls 150' in page.locator('.context-detail-tool-components').inner_text()
    tool_row = page.locator('.context-detail-raw-row').nth(2)
    tool_row.locator('summary').click()
    page.wait_for_function("document.querySelectorAll('.context-detail-raw-row code')[2]?.textContent.includes('tool_call_id')")
    raw = tool_row.locator('code').inner_text()
    assert '"tool_call_id": "r"' in raw and '"context_meta"' not in raw and '"activity"' in raw
    assert '<img src=x' in raw and tool_row.locator('img').count() == 0
    import json
    parsed = json.loads(raw)
    assert parsed == {'role':'tool','tool_call_id':'r','content':'<img src=x onerror=alert(1)> synthetic','activity':{'version':1,'ok':True,'meta':{'lines':1,'bytes':len('<img src=x onerror=alert(1)> synthetic')}}}
    assert tool_row.locator('.context-json-key').count() > 0 and tool_row.locator('.context-json-string').count() > 0
    assert tool_row.locator('.context-detail-raw-role').evaluate('(e)=>getComputedStyle(e).color') == page.locator('.activity-card.tool .activity-card-header').first.evaluate('(e)=>getComputedStyle(e).color')
    # Replay uses the same IDs, counts, outcomes and result bodies with no POST.
    def transcript():
        # Live reasoning is deliberately lazy while collapsed; compare visible
        # expanded content, not an unrendered hidden body.
        card = page.locator('#messages .activity-card.reasoning.collapsed .activity-card-header')
        if card.count():
            card.click()
        return page.locator('#messages').evaluate('''root => [...root.children].filter(e=>e.matches('.message,.activity-card')).map(e=>({
          type:e.classList.contains('group')?'group':e.classList.contains('reasoning')?'reasoning':e.classList.contains('user')?'user':'assistant',
          text:e.classList.contains('reasoning')?e.querySelector('.activity-card-body').textContent:
               e.classList.contains('group')?[...e.querySelectorAll('.group-count,.activity-card-body,.approval-state')].map(n=>n.textContent).join('|'):
               e.querySelector('.message-body')?.textContent||''}))''')
    live = transcript()
    assert any('after' == item['text'] for item in live)
    page.locator('.session-item[data-session-id=B]').click()
    wait_count(page, '3.4k')
    wait_raw(page, 'No stored messages · Showing 0 of 0')
    assert not page.locator('#messages .group-count').count()
    page.locator('.session-item[data-session-id=A]').click()
    wait_count(page, '2.7k')
    wait_raw(page, 'Showing 5 of 5')
    page.wait_for_function("document.querySelectorAll('#messages .group-count').length===2")
    assert transcript() == live, (live, transcript())
    assert page.locator('#messages .approval-controls button').count() == 0
    assert not requests(page, '/api/approval')
    assert len(requests(page, '/api/chat')) == 1
    assert page.locator('#contextPanel').is_visible()
    assert not errors, errors
    page.close()
    print('combined: actual bootstrap + transcript parity + snapshot authority + raw metadata + session switch: PASS')

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=args.browser,headless=True)
    print('Chromium',browser.version)
    if MODE=='original':check_original(browser)
    else:
        if MODE in ('lifecycle','fixed'):check_lifecycle(browser)
        if MODE in ('raw','fixed'):check_raw(browser)
        if MODE in ('layout','fixed'):check_layout(browser)
        if MODE=='combined':check_combined(browser)
    browser.close()
