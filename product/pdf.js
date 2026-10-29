const { chromium } = require('playwright');
(async()=>{const b=await chromium.launch();const p=await b.newPage();
await p.goto('file://'+process.cwd()+'/guide/guide.html');
await p.pdf({path:'User_Guide_Tax_Tracker.pdf',format:'A4',margin:{top:'18mm',bottom:'18mm',left:'18mm',right:'18mm'}});await b.close()})();
