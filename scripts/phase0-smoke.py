import json,time,urllib.request,re
K=[l.split('=',1)[1].strip().strip('"\'') for l in open('/path/to/env-with-LEMONADE_API_KEY') if l.startswith('LEMONADE_API_KEY=')][0]
def run(temp,seed):
    b={"model":"Gemma-4-E4B-it-GGUF","messages":[{"role":"user","content":"Reply with one short sentence about rivers."}],"temperature":temp,"seed":seed,"max_tokens":64,"stream":True,"stream_options":{"include_usage":True}}
    r=urllib.request.Request("http://10.138.6.1:13305/api/v1/chat/completions",json.dumps(b).encode(),{"Authorization":"Bearer "+K,"Content-Type":"application/json"})
    t0=time.time();first=None;txt="";last=None;keys=set();n=0
    for line in urllib.request.urlopen(r,timeout=120):
        line=line.decode().strip()
        if not line.startswith("data:") or line.endswith("[DONE]"): continue
        j=json.loads(line[5:]);n+=1;keys|=set(j.keys())
        ch=(j.get("choices") or [{}])[0];d=ch.get("delta",{})
        c=d.get("content") or d.get("reasoning_content")
        if c and first is None: first=time.time()-t0
        txt+=d.get("content") or ""
        if j.get("usage") or j.get("timings"): last=j
    return dict(temp=temp,seed=seed,ttft_client=round(first or -1,3),total=round(time.time()-t0,3),events=n,text=txt[:90],usage=last.get("usage") if last else None,timings=last.get("timings") if last else None,topkeys=sorted(keys))
for t,s in [(0.0,17),(0.0,43),(0.7,17),(0.7,43)]:
    print(json.dumps(run(t,s)))
