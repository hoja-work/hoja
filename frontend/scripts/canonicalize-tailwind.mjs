#!/usr/bin/env node
/** Canonicalize equivalent inline declarations without changing the app's spacing theme.
 * --write updates source; the default audits and exits nonzero if changes are available.
 * Pixel lengths intentionally stay pixels: the app's root is 14px and supports font scaling.
 */
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');
const sizes = {width:'w',height:'h','min-width':'min-w','max-width':'max-w','min-height':'min-h','max-height':'max-h',margin:'m','margin-top':'mt','margin-right':'mr','margin-bottom':'mb','margin-left':'ml',padding:'p','padding-top':'pt','padding-right':'pr','padding-bottom':'pb','padding-left':'pl',gap:'gap','row-gap':'gap-y','column-gap':'gap-x',top:'top',right:'right',bottom:'bottom',left:'left',inset:'inset','text-indent':'indent','scroll-margin':'scroll-m','scroll-padding':'scroll-p'};
const keywords = {
 display:{flex:'flex','inline-flex':'inline-flex',grid:'grid','inline-grid':'inline-grid',block:'block','inline-block':'inline-block',inline:'inline',none:'hidden',contents:'contents','flow-root':'flow-root',table:'table','table-row':'table-row','table-cell':'table-cell'},
 position:{absolute:'absolute',relative:'relative',fixed:'fixed',sticky:'sticky',static:'static'},
 'align-items':{center:'items-center','flex-start':'items-start','flex-end':'items-end',baseline:'items-baseline',stretch:'items-stretch'},
 'align-self':{auto:'self-auto',center:'self-center','flex-start':'self-start','flex-end':'self-end',stretch:'self-stretch',baseline:'self-baseline'},
 'justify-content':{center:'justify-center','flex-start':'justify-start','flex-end':'justify-end','space-between':'justify-between','space-around':'justify-around','space-evenly':'justify-evenly'},
 'justify-items':{center:'justify-items-center',start:'justify-items-start',end:'justify-items-end',stretch:'justify-items-stretch'},
 'justify-self':{auto:'justify-self-auto',center:'justify-self-center',start:'justify-self-start',end:'justify-self-end',stretch:'justify-self-stretch'},
 'align-content':{center:'content-center',start:'content-start',end:'content-end','space-between':'content-between','space-around':'content-around','space-evenly':'content-evenly',baseline:'content-baseline',stretch:'content-stretch'},
 'flex-direction':{row:'flex-row',column:'flex-col','row-reverse':'flex-row-reverse','column-reverse':'flex-col-reverse'},
 'flex-wrap':{wrap:'flex-wrap',nowrap:'flex-nowrap','wrap-reverse':'flex-wrap-reverse'},
 'place-items':{center:'place-items-center',start:'place-items-start',end:'place-items-end',stretch:'place-items-stretch',baseline:'place-items-baseline'},
 cursor:{pointer:'cursor-pointer',default:'cursor-default',auto:'cursor-auto',grab:'cursor-grab',grabbing:'cursor-grabbing',wait:'cursor-wait','not-allowed':'cursor-not-allowed',text:'cursor-text',move:'cursor-move',none:'cursor-none',help:'cursor-help',progress:'cursor-progress'},
 overflow:{auto:'overflow-auto',hidden:'overflow-hidden',visible:'overflow-visible',clip:'overflow-clip',scroll:'overflow-scroll'},
 'overflow-x':{auto:'overflow-x-auto',hidden:'overflow-x-hidden',visible:'overflow-x-visible',clip:'overflow-x-clip',scroll:'overflow-x-scroll'},
 'overflow-y':{auto:'overflow-y-auto',hidden:'overflow-y-hidden',visible:'overflow-y-visible',clip:'overflow-y-clip',scroll:'overflow-y-scroll'},
 'overscroll-behavior':{auto:'overscroll-auto',contain:'overscroll-contain',none:'overscroll-none'},
 'white-space':{nowrap:'whitespace-nowrap',normal:'whitespace-normal',pre:'whitespace-pre','pre-wrap':'whitespace-pre-wrap','pre-line':'whitespace-pre-line','break-spaces':'whitespace-break-spaces'},
 'text-overflow':{ellipsis:'text-ellipsis',clip:'text-clip'},
 'text-align':{left:'text-left',right:'text-right',center:'text-center',justify:'text-justify',start:'text-start',end:'text-end'},
 'font-style':{italic:'italic',normal:'not-italic'},
 'text-transform':{uppercase:'uppercase',lowercase:'lowercase',capitalize:'capitalize',none:'normal-case'},
 'text-decoration-line':{none:'no-underline',underline:'underline','line-through':'line-through',overline:'overline'},
 'pointer-events':{none:'pointer-events-none',auto:'pointer-events-auto'},
 'user-select':{none:'select-none',text:'select-text',all:'select-all',auto:'select-auto'},
 'object-fit':{cover:'object-cover',contain:'object-contain',fill:'object-fill',none:'object-none','scale-down':'object-scale-down'},
 resize:{none:'resize-none',vertical:'resize-y',horizontal:'resize-x',both:'resize'},
 appearance:{none:'appearance-none',auto:'appearance-auto'},
 isolation:{isolate:'isolate',auto:'isolation-auto'},
 'box-sizing':{'border-box':'box-border','content-box':'box-content'},
 'list-style-type':{none:'list-none',disc:'list-disc',decimal:'list-decimal'},
 'border-style':{solid:'border-solid',dashed:'border-dashed',dotted:'border-dotted',double:'border-double',none:'border-none',hidden:'border-hidden'},
 'border-collapse':{collapse:'border-collapse',separate:'border-separate'},
 'font-variant-numeric':{'tabular-nums':'tabular-nums','lining-nums':'lining-nums','oldstyle-nums':'oldstyle-nums'},
 visibility:{hidden:'invisible',visible:'visible',collapse:'collapse'},
 'scroll-behavior':{auto:'scroll-auto',smooth:'scroll-smooth'},
};
const colors = {'var(--bg)':'bg','var(--panel)':'panel','var(--text)':'foreground','var(--muted)':'muted','var(--line)':'line','var(--subtle)':'subtle','var(--accent)':'accent','var(--accent-soft)':'accent-soft','var(--danger)':'danger','var(--sidebar-bg)':'sidebar',transparent:'transparent',currentColor:'current',inherit:'inherit','#fff':'white','#ffffff':'white','#000':'black','#000000':'black'};
const fonts = {100:'thin',200:'extralight',300:'light',400:'normal',500:'medium',600:'semibold',700:'bold',800:'extrabold',900:'black',normal:'normal',bold:'bold'};
function length(prefix, value) {
  if (/^0(?:px|rem|em|%)?$/.test(value)) return prefix+'-0';
  if (value==='auto') return prefix+'-auto';
  if (value==='1px') return prefix+'-px';
  if (value==='-1px') return '-'+prefix+'-px';
  const rem=value.match(/^(-?\d*\.?\d+)rem$/);
  if (rem) {const n=Number(rem[1])*4;if(Number.isInteger(n*2))return (n<0?'-':'')+prefix+'-'+Math.abs(n);}
  const aliases={'100%':'full','100dvh':'dvh','100vh':'screen','min-content':'min','max-content':'max','fit-content':'fit'};
  // screen on widths means 100vw, not 100vh; other viewport lengths stay arbitrary.
  if (value==='100vh'&&!/^(?:h|min-h|max-h)$/.test(prefix)) return prefix+'-[100vh]';
  if (value==='100dvh'&&!/^(?:h|min-h|max-h)$/.test(prefix)) return prefix+'-[100dvh]';
  if (aliases[value]&&/^(?:w|h|min-w|max-w|min-h|max-h|m[trbl]?|inset|top|left|right|bottom)$/.test(prefix)) return prefix+'-'+aliases[value];
  return prefix+'-['+value+']';
}
function color(prefix,value){return prefix+'-'+(colors[value]||'['+value+']');}
function dimensionShorthand(prop,value){
  const parts=value.split('_');if(parts.length>4||parts.some(x=>!x||/[(),]/.test(x)))return null;
  const [t,r=t,b=t,l=r]=parts;
  const pre=prop==='margin'?'m':'p';
  if(t===r&&r===b&&b===l)return length(pre,t);
  if(t===b&&r===l)return length(pre+'y',t)+' '+length(pre+'x',r);
  if(r===l)return length(pre+'t',t)+' '+length(pre+'x',r)+' '+length(pre+'b',b);
  return [t,r,b,l].map((v,i)=>length(pre+'trbl'[i],v)).join(' ');
}
export function propertyUtility(prop,value){
  if(keywords[prop]?.[value])return keywords[prop][value];
  if((prop==='padding'||prop==='margin')&&value.includes('_'))return dimensionShorthand(prop,value)||length(sizes[prop],value);
  if(sizes[prop])return length(sizes[prop],value);
  if(prop==='background-color'||prop==='color'||prop==='border-color'||prop==='outline-color'||prop==='accent-color')return color({'background-color':'bg',color:'text','border-color':'border','outline-color':'outline','accent-color':'accent'}[prop],value);
  // Solid background shorthands use the named color utility. Keep image/reset shorthands intact.
  if(prop==='background'&&(colors[value]||/^(?:#[0-9a-fA-F]{3,8}|(?:rgb|hsl|oklch|color-mix|var)\()/.test(value))&&!/(?:gradient|url)\(/.test(value))return color('bg',value);
  if(prop==='font-family')return value.includes('&quot;')?null:'font-['+value+']';
  if(prop==='text-decoration'&&['none','underline','line-through','overline'].includes(value))return {none:'no-underline',underline:'underline','line-through':'line-through',overline:'overline'}[value];
  if(prop==='list-style'&&value==='none')return 'list-none';
  if(prop==='font-size')return 'text-['+(/^(?:var\(|--)/.test(value)?'length:':'')+value+']';
  if(prop==='font-weight')return 'font-'+(fonts[value]||'['+value+']');
  if(prop==='line-height')return value==='1'?'leading-none':'leading-['+value+']';
  if(prop==='letter-spacing')return value==='0'?'tracking-normal':'tracking-['+value+']';
  const side=prop.match(/^border-(top|right|bottom|left)$/);
  if(side&&/^(?:0|0px)$/.test(value))return 'border-'+{top:'t',right:'r',bottom:'b',left:'l'}[side[1]]+'-0';
  if(side&&/^\d+(?:\.\d+)?px_(?:solid|dashed|dotted)_/.test(value)){
    const [,width,style,ink]=value.match(/^(\d+(?:\.\d+)?px)_(solid|dashed|dotted)_(.+)$/),p='border-'+{top:'t',right:'r',bottom:'b',left:'l'}[side[1]];
    // Border style is global in Tailwind, so leave non-solid side shorthands intact.
    if(style==='solid')return (width==='1px'?p:p+'-['+width+']')+' '+color(p,ink);
  }
  if(prop==='outline'&&/^\d+(?:\.\d+)?px_solid_/.test(value)){
    const [,width,ink]=value.match(/^(\d+(?:\.\d+)?px)_solid_(.+)$/);return 'outline-solid outline-['+width+'] '+color('outline',ink);
  }
  if(prop==='border-width')return value==='0'||value==='0px'?'border-0':value==='1px'?'border':'border-['+value+']';
  if(prop==='border'&&/^(?:0|0px|none)$/.test(value))return value==='none'?'border-none':'border-0';
  if(prop==='border'&&/^\d+(?:\.\d+)?px_(?:solid|dashed|dotted)_/.test(value)){
    const [,width,style,ink]=value.match(/^(\d+(?:\.\d+)?px)_(solid|dashed|dotted)_(.+)$/);
    return (width==='1px'?'border':'border-['+width+']')+' border-'+style+' '+color('border',ink);
  }
  if(prop==='border-radius')return value==='0'||value==='0px'?'rounded-none':'rounded-['+value+']';
  if(prop==='box-shadow')return value==='none'?'shadow-none':'shadow-['+value+']';
  if(prop==='opacity')return Number.isFinite(Number(value))?'opacity-'+Number((Number(value)*100).toFixed(8)):'opacity-['+value+']';
  if(prop==='z-index')return /^\d+$/.test(value)?'z-'+value:value==='auto'?'z-auto':'z-['+value+']';
  if(prop==='grow')return value==='1'?'grow':'grow-['+value+']';
  if(prop==='shrink')return value==='1'?'shrink':value==='0'?'shrink-0':'shrink-['+value+']';
  if(prop==='flex')return value==='none'?'flex-none':value==='auto'||value==='1_1_auto'?'flex-auto':value==='initial'||value==='0_1_auto'?'flex-initial':'flex-['+value+']';
  if(prop==='filter')return value==='none'?'filter-none':'filter-['+value+']';
  if(prop==='transform')return value==='none'?'transform-none':'transform-['+value+']';
  if(prop==='overflow-wrap')return {anywhere:'wrap-anywhere','break-word':'wrap-break-word',normal:'wrap-normal'}[value]||null;
  if(prop==='flex-basis')return length('basis',value);
  if(prop==='grid-template-columns')return /^repeat\((\d+),minmax\(0,1fr\)\)$/.test(value)?'grid-cols-'+value.match(/^repeat\((\d+)/)[1]:value==='none'?'grid-cols-none':'grid-cols-['+value+']';
  if(prop==='grid-template-rows')return value==='none'?'grid-rows-none':'grid-rows-['+value+']';
  if(prop==='grid-column')return 'col-['+value+']';
  if(prop==='grid-row')return 'row-['+value+']';
  if(prop==='order')return /^-?\d+$/.test(value)?(Number(value)<0?'-order-'+Math.abs(Number(value)):'order-'+value):'order-['+value+']';
  if(prop==='outline-offset')return /^-?\d+(?:px)?$/.test(value)?(value.startsWith('-')?'-':'')+'outline-offset-'+Math.abs(parseFloat(value)):'outline-offset-['+value+']';
  if(prop==='transition-duration')return value.endsWith('ms')?'duration-['+value+']':'duration-['+value+']';
  if(prop==='transition-timing-function')return 'ease-['+value+']';
  if(prop==='transition-property')return 'transition-['+value+']';
  if(prop==='animation')return value==='none'?'animate-none':'animate-['+value+']';
  if(prop==='content'&&!value.includes('&quot;'))return 'content-['+value+']';
  return null;
}
/** Split whitespace only outside brackets (selectors and arbitrary values stay intact). */
function tokens(text){return text.match(/(?:[^\s\[\]]|\[(?:[^\[\]]|\[[^\[\]]*\])*\])+/g)||[];}
function canonicalToken(token){
  // Locate the final top-level segment, leaving variants such as [&:hover]: untouched.
  let depth=0,start=0;for(let i=0;i<token.length;i++){if(token[i]==='[')depth++;else if(token[i]===']')depth--;else if(token[i]===':'&&depth===0)start=i+1;}
  const variant=token.slice(0,start).replace(/(^|:)first-child:/g,'$1first:').replace(/(^|:)last-child:/g,'$1last:');let base=token.slice(start),important='';
  if(base.endsWith('!')){base=base.slice(0,-1);important='!';}
  if(base.startsWith('!')){base=base.slice(1);important='!';}
  const match=base.match(/^\[([a-z-]+):(.+)\]$/);let result;
  if(match)result=propertyUtility(match[1],match[2]);
  else {
    // Existing length utilities can use exact zero/rem aliases as well.
    const m=base.match(/^(-?)([a-z-]+)-\[(-?\d*\.?\d+(?:px|rem|em|%)?)\]$/);
    if(m&&new Set([...Object.values(sizes),'px','py','mx','my']).has(m[2]))result=length(m[2],(m[1]||'')+m[3]);
  }
  return result?result.split(' ').map(x=>variant+x+important).join(' '):token;
}
export function canonicalize(text){return tokens(text).map(canonicalToken).join(' ');}
export function audit(write=false){
 let changed=0,sites=0,candidates=new Map();
 function walk(dir){for(const ent of fs.readdirSync(dir,{withFileTypes:true})){const p=path.join(dir,ent.name);if(ent.isDirectory())walk(p);else if(/\.tsx?$/.test(p)){
  const source=fs.readFileSync(p,'utf8'),sf=ts.createSourceFile(p,source,ts.ScriptTarget.Latest,true,p.endsWith('x')?ts.ScriptKind.TSX:ts.ScriptKind.TS),edits=[];
  function visit(n){if(ts.isStringLiteral(n)||ts.isNoSubstitutionTemplateLiteral(n)||ts.isTemplateHead(n)||ts.isTemplateMiddle(n)||ts.isTemplateTail(n)){
    // Only class-bearing strings. Ordinary identifiers, selectors and runtime strings are untouched.
    const start=n.getStart(sf),raw=source.slice(start,n.end),head=1,tail=(ts.isTemplateHead(n)||ts.isTemplateMiddle(n))?2:1;
    const body=raw.slice(head,-tail);if(!/\[[a-z-]+:/.test(body)&&!/[a-z]-\[-?\d/.test(body))return;
    const before=tokens(body),after=before.map(canonicalToken);
    before.forEach((x,i)=>{if(x!==after[i])candidates.set(x,after[i]);});
    if(before.some((x,i)=>x!==after[i])){let next=after.join(' ');if(/^\s/.test(body))next=' '+next;if(/\s$/.test(body))next+=' ';edits.push([start+head,n.end-tail,next]);sites++;}return;
  }ts.forEachChild(n,visit);}visit(sf);
  if(edits.length){let out=source;for(const[a,b,v]of edits.sort((a,b)=>b[0]-a[0]))out=out.slice(0,a)+v+out.slice(b);if(write)fs.writeFileSync(p,out);changed++;}
 }}}walk(root);
 return {files:changed,sites,candidates:[...candidates].map(([from,to])=>({from,to}))};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const result=audit(process.argv.includes('--write'));console.log(JSON.stringify(result,null,2));
 if(result.files&&!process.argv.includes('--write'))process.exitCode=1;
}
