// Read-only local relay for the production commit's fixed companion port.
import http from 'node:http';
http.createServer((req,res)=>{
 if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405);res.end();return;}
 const upstream=http.request(new URL(req.url,process.argv[3]??'http://127.0.0.1:5195'),{method:req.method,headers:{...req.headers,host:'127.0.0.1:5195'}},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res);});
 upstream.on('error',e=>{res.writeHead(502);res.end(e.message);});upstream.end();
}).listen(Number(process.argv[2]??5965),'127.0.0.1');
