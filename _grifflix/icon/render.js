const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();const [html,out,size,round]=process.argv.slice(2);
const p=await b.newPage({viewport:{width:+(process.argv[7]||512),height:+(process.argv[6]||512)},deviceScaleFactor:(+size)/(process.argv[6]||512)});await p.goto('file://'+html);await p.waitForSelector('body[data-ready]');await p.waitForTimeout(300);
if(round)await p.addStyleTag({content:'.i{border-radius:115px}'});await (await p.$('#i')).screenshot({path:out,omitBackground:true});await b.close();})();
