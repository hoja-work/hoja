#!/usr/bin/env node
/** Tailwind rendering contract. Builds the CURRENT ReviewsShell and shared primitives
 * through Vite's actual Tailwind plugin. CDP is required for the full check; a missing
 * browser is an error, never a silently passing source-only test. Pixel measurements
 * from the original stylesheet are the contract, independent of root font size.
 */
import {readFileSync, writeFileSync, unlinkSync} from 'node:fs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const exec = promisify(execFile);
import {dirname, join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
const frontend = join(dirname(fileURLToPath(import.meta.url)), '..');
const repo = join(frontend, '../..');

export async function checkRenderedDesign({selfTest = false} = {}) {
  const {createServer} = await import('vite');
  const tsx = readFileSync(join(frontend, 'src/main.tsx'), 'utf8');
  const from = tsx.indexOf('function ReviewGlyph(');
  const to = tsx.indexOf('\nfunction App(', from);
  if (from < 0 || to < 0) throw new Error('ReviewsShell fixture extraction failed');
  const icons = tsx.match(/import \{[^\n]+\} from 'lucide-react';/)[0];
  const id = `.reviews-check-${process.pid}`;
  const entry = join(frontend, `${id}.tsx`), html = join(frontend, `${id}.html`);
  // Only app-local services are replaced. The rendered shell, event handlers,
  // glyphs, Markdown, CSS cascade and shared primitives are the actual source.
  writeFileSync(entry, `import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
${icons}
import {SplitPane,ScrollArea,TabStrip,Tab,GroupBar,ListRow,PropertyRow,EmptyState,Composer} from './src/design';
import {reviewGroupsOf} from './src/reviews-gate';
import {reviewGlyphPaths} from './src/reviews-glyphs';
import {Markdown} from './src/markdown';
import './src/tailwind.css'; import './src/styles.css';
const ReviewActions=()=>null; const gql=()=>Promise.resolve({});
${tsx.slice(from, to)}
const base={kind:'pull_request',key:'TEST-1',externalId:'#123',url:'https://github.com/example/repo/pull/123',title:'Rendered review fixture',author:'fixture',createdAt:new Date().toISOString(),state:'open',mergeState:'clean',checksState:'passing',reviewDecision:'approved'};
const links=[{...base,id:'one',additions:7,deletions:3,baseRef:'main',headRef:'feature',ahead:2,behind:1,reviewers:[{login:'reviewer',state:'approved'}],checksTotal:4,checksPassing:3,checksFailing:1,body:'Stored description',files:[{path:'long.ts',previousPath:'old.ts',status:'renamed',additions:1,deletions:1,patch:'@@ hunk @@\\n+'+'x'.repeat(400)+'\\n-deleted\\n context'},{path:'binary.bin',status:'added',additions:0,deletions:0,patch:null}]},{...base,id:'two',title:'Unknown review',state:'draft',files:[]}];
createRoot(document.getElementById('root')!).render(<><div data-library-fixture="" style={{position:"fixed",bottom:0,right:0,width:300,zIndex:-1}}><Composer value="" placeholder="Comment" sendIcon={<span>Send</span>} onChange={()=>{}} onSend={()=>{}}/></div><div className="app"><div className="body" data-app-body=""><nav className="sidebar w-60 flex-none h-dvh route-review:relative route-review:top-auto route-review:self-stretch route-review:col-auto route-review:row-auto review-drawer:route-review:fixed! review-drawer:route-review:z-35! review-drawer:route-review:top-0! review-drawer:route-review:bottom-0! review-drawer:route-review:left-0! review-drawer:route-review:-translate-x-full! review-drawer:route-review:shadow-xl! review-drawer:route-review:[&.open]:translate-x-0!">Sidebar</nav><main className="main"><ReviewsShell links={links} onOpenNav={()=>document.querySelector('.sidebar')?.classList.toggle('open')}/></main></div></div></>);
`);
  writeFileSync(html, `<!doctype html><html><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module" src="/${id}.tsx"></script></body></html>`);
  let server;
  try {
    server = await createServer({root: frontend, configFile: join(frontend, 'vite.config.ts'), server:{port:0,host:'127.0.0.1'}});
    await server.listen();
    const port = server.httpServer.address().port;
    const measure = async (width, expr) => {
      let output;
      try { output = await exec(process.execPath,[join(repo,'scripts/ui-probes/cdp-tab.mjs'),'--url',`http://127.0.0.1:${port}/${id}.html`,'--viewport',`${width}x900`,'--wait','[data-reviews-row-main]','--settle','500','--expr',expr],{encoding:'utf8',maxBuffer:8e6}); } catch (e) {
        let detail; try { detail=JSON.parse(e.stdout).error; } catch {}
        throw new Error(detail || 'CDP browser check failed: '+(e.stderr || e.message).slice(0,500));
      }
      const result = JSON.parse(output.stdout);
      if (result.error || !result.value) throw new Error(result.error || 'No rendered result (check Vite/browser console)');
      if (result.value.length) throw new Error(`Rendered Reviews (${width}px):\n${result.value.join('\n')}`);
    };
    const layoutFailures=[];
    for (const width of [1440,1228,1227,1024,890,889,390]) {
      try { await measure(width, probe(width, null)); } catch(e) { layoutFailures.push(e.message); }
    }
    if(layoutFailures.length) throw new Error(layoutFailures.join('\n\n'));
    if (selfTest) {
      for (const [mutation, message] of [['row','row height'],['band','group height'],['margin','group left margin'],['glyph','glyph border'],['size','glyph SVG size']]) {
        let rejected = false;
        try { await measure(1440, probe(1440, mutation)); } catch (e) { if (String(e).includes(message)) rejected = true; else throw e; }
        if (!rejected) throw new Error(`Mutation self-test: ${mutation} regression was not detected`);
        console.log(`rendering mutation self-test ok: ${mutation} regression detected`);
      }
    }
    console.log('Tailwind rendered checks ok: 7 viewport boundaries, local selection, tabs/keyboard, diff scrolling, empty states, drawer and overlay');
  } finally {
    await server?.close();
    for (const p of [entry,html]) try { unlinkSync(p); } catch {}
  }
}

function probe(width, mutate) { return `return (async()=>{
const failures=[];
const q=(s)=>document.querySelector(s), all=(s)=>[...document.querySelectorAll(s)];
const hook=(name)=>q('[data-reviews-'+name+']');
const check=(yes,msg)=>{if(!yes)failures.push(msg)};
const near=(got,want,msg)=>check(Math.abs(got-want)<.6,msg+': '+got+' expected '+want);
const cs=(el)=>getComputedStyle(el);
const tick=()=>new Promise(r=>setTimeout(r,80));
const row=hook('row'), pane=q('[data-split-detail]'), col=q('[data-split-ledger]');
check(!!row && !!hook('page'),'primitive data hooks must reach rendered DOM');
if(!row || !pane || !col)return failures.concat('missing split/list DOM');
const composer=q('[data-library-fixture] form');
near(composer.getBoundingClientRect().height,${width}>600?48:44,'exact responsive Composer height');
check(!!composer.querySelector('input') && !!composer.querySelector('button[type=submit]'),'Composer input/send controls');
const mutation=${JSON.stringify(mutate)};
if(mutation==='row')row.style.height='1px';
if(mutation==='band')hook('group-head').style.height='1px';
if(mutation==='margin')hook('group-head').style.marginLeft='0px';
if(mutation==='glyph')hook('glyph').style.border='2px solid red';
if(mutation==='size')hook('glyph').querySelector('svg').style.width='12px';
near(row.getBoundingClientRect().height,40,'original row height 40px');
near(parseFloat(cs(row).fontSize),13,'original review row font size 13px');
check(cs(row).borderTopWidth==='0px' && cs(row).marginBottom==='0px','rows flat, borderless, no card spacing');
near(hook('group-head').getBoundingClientRect().height,28,'original group height 28px');
check(cs(hook('group-head')).marginTop==='0px' && cs(hook('group-head')).marginBottom==='0px','group bands have no vertical card margin');
near(parseFloat(cs(hook('group-head')).borderRadius),8,'original group radius 8px');
near(parseFloat(cs(hook('group-head')).marginLeft),10,'original group left margin 10px');
check(cs(hook('glyph')).borderTopWidth==='0px','glyph border must stay absent (filled vector)');
near(hook('glyph').getBoundingClientRect().width,14,'original glyph width 14px');
check(hook('glyph').querySelectorAll('path').length===4,'open glyph filled path set');
near(hook('glyph').querySelector('svg').getBoundingClientRect().width,14,'glyph SVG size');
check(hook('row-main').tagName==='BUTTON','local selection uses a button');
check(hook('row-ext').tagName==='A' && hook('row-ext').target==='_blank','external sibling GitHub link');
check(cs(pane).display===(${width}>1227?'flex':'none'),'empty detail breakpoint');
check(!!hook('pane-empty'),'select-a-review empty state');
if(${width}>1227){near(parseFloat(cs(col).maxWidth),482,'original ledger width cap 482px');check(col.getBoundingClientRect().width<=482.6,'ledger respects width cap');}
else check(col.getBoundingClientRect().width>q('.main').getBoundingClientRect().width-3,'one-pane ledger fills available width');
const sidebar=q('.sidebar');
if(${width}<=889){check(cs(sidebar).position==='fixed' && sidebar.getBoundingClientRect().right<=0,'sidebar off-canvas drawer'); hook('nav-btn').click();await new Promise(r=>setTimeout(r,300));check(sidebar.getBoundingClientRect().left>=-1,'navigation button opens drawer'); sidebar.classList.remove('open');}
hook('row-main').click();await tick();
check(hook('row-main').getAttribute('aria-pressed')==='true','click selects locally');
check(!!hook('detail') && cs(pane).display!=='none','selected detail visible');
if(${width}<=1227){check(cs(pane).position==='absolute','detail overlay position');near(pane.getBoundingClientRect().width,hook('page').getBoundingClientRect().width,'overlay fills page');}
near(hook('detail-head').getBoundingClientRect().height,${width}<=600?48:52,'original detail breadcrumb height');
near(parseFloat(cs(hook('detail-title')).fontSize),${width}<=600?20:24,'original detail title font size');
near(parseFloat(cs(hook('detail-title')).lineHeight),${width}<=600?28:32,'original detail title line height');
near(hook('detail-tabs').getBoundingClientRect().height,44,'original pane tabs height 44px');
near(hook('prop').querySelector('dt').getBoundingClientRect().width,96,'original property label width 96px');
check(hook('detail').textContent.includes('Stored description'),'stored description rendered');
for(const text of ['3 passing','1 failing','4 total','main','feature','reviewer'])check(hook('detail').textContent.includes(text),'stored detail '+text);
const overview=q('#reviews-tab-overview'),diff=q('#reviews-tab-diff');
overview.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await tick();
check(diff.getAttribute('aria-selected')==='true' && document.activeElement===diff,'keyboard selects/focuses Diff');
check(q('[role="tabpanel"]').getAttribute('aria-labelledby')===diff.id,'tabpanel labelled by selected tab');
check(hook('diff-summary').textContent.includes('2 files changed'),'stored file summary');
check(hook('diff-file-from').textContent.includes('old.ts'),'rename previous path');
const patch=hook('diff-patch');
check(cs(patch).overflowX==='auto' && patch.scrollWidth>patch.clientWidth,'long patch scrolls internally');
const add=q('[data-kind="add"]'),del=q('[data-kind="del"]');
check(cs(add).whiteSpace==='pre','patch indentation preserved');
check(cs(add).color!==cs(del).color,'addition/deletion colors distinct');
all('[data-reviews-diff-file-head]')[1].click();await tick();
check(hook('diff-none').textContent.includes('Binary file or too large'),'null patch explicit state');
const file=hook('diff-file-head');file.click();await tick();check(file.getAttribute('aria-expanded')==='false','file collapses');file.click();await tick();check(file.getAttribute('aria-expanded')==='true','file expands');
diff.dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true}));await tick();check(overview.getAttribute('aria-selected')==='true','Home restores Overview');
if(${width}<=1227){hook('pane-close').click();await tick();check(cs(pane).display==='none','back dismisses overlay');}
else {hook('row-main').click();await tick();check(!!hook('pane-empty'),'selected row toggles empty state');}
all('[data-reviews-row-main]')[1].click();await tick();
check(hook('detail').textContent.includes('no checks recorded') && hook('detail').textContent.includes('No pull request description is stored'),'unknown data explicit states');
q('#reviews-tab-diff').click();await tick();check(hook('diff-empty').textContent.includes('No file data stored'),'absent files explicit state');
check(document.documentElement.scrollWidth<=innerWidth+1,'page must not overflow horizontally');
check(hook('page').getBoundingClientRect().bottom<=innerHeight+1,'page height derives from viewport shell');
return failures;
})();`; }

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.includes('--no-browser')) {
    console.log('SKIPPED rendering checks (--no-browser); CDP on 9222 is required for test:design');
  } else await checkRenderedDesign({selfTest:process.argv.includes('--self-test')});
}
