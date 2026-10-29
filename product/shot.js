const { chromium } = require('playwright'); const fs=require('fs');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1600,height:1600}});
for(const f of fs.readdirSync('_html')){await p.goto('file://'+process.cwd()+'/_html/'+f);await p.screenshot({path:'images/'+f.replace('.html','.png')});console.log(f)}
await b.close()})();
