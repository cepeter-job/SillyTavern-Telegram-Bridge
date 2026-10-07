// Document-local recovery. Never persist request bodies, credentials or story text.
const activeStates=new Set(['queued','running']);
const finalStates=new Set(['succeeded','failed','interrupted']);
const transient=error=>!error.status||error.status===429||error.status>=500;
const fault=(message,status)=>Object.assign(new Error(message),{status});
function canonical(value) {
  if(Array.isArray(value))return value.map(canonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])]));
  return value;
}
async function fingerprint(path,body) {
  const bytes=new TextEncoder().encode(JSON.stringify([path,canonical(body)]));
  const digest=await crypto.subtle.digest('SHA-256',bytes);
  return Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
}

export function createJobController({request,identity,now=Date.now,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms)),
  pollInterval=1500,retryDelays=[500,1500,3000],maxWait=600000,horizon=3600000,capacity=32}) {
  const records=new Map();
  const actor=()=>String(identity());
  function check(record) {
    if(record.actor!==actor())throw fault('Reopen the app from the original account to check this operation.',403);
    if(now()-record.created>=horizon)throw fault('Recovery window expired. Review Operations; do not repeat an unconfirmed action.',410);
    if(record.blocked)throw Object.assign(new Error(record.error.message),record.error);
  }
  function emit(record,state) {
    const event={operationId:record.id,jobId:record.job?.id,state,lastState:record.job?.state};
    for(const notify of record.observers)try{notify(event);}catch{/* A view cannot change the saved operation outcome. */}
  }
  function remember(record) {
    for(const [id,entry] of records)if(records.size>=capacity&&entry.final&&!entry.flight)records.delete(id);
    if(records.size>=capacity)throw fault('Too many unresolved operations. Review Operations before starting another.',429);
    records.set(record.id,record);return record;
  }
  function make(id,key) {
    return {id,key,actor:actor(),created:now(),submitted:false,job:null,final:false,blocked:false,flight:null,observers:new Set()};
  }
  function accept(record,job) {
    check(record);
    if(!job||typeof job.id!=='string'||!job.id||job.id.length>64||!activeStates.has(job.state)&&!finalStates.has(job.state)) {
      throw fault('The bridge returned an unrecognized operation status. Review Operations.',502);
    }
    if(record.job?.id&&record.job.id!==job.id)throw fault('The operation identity changed unexpectedly. Review Operations.',502);
    record.job=job;record.final=finalStates.has(job.state);emit(record,job.state);
    if(record.final&&job.state!=='succeeded')throw fault(job.error||'Operation did not complete.',422);
    return job;
  }
  function annotate(record,error) {
    const terminal=record.final||!record.job&&!transient(error)&&!record.submitted;
    error.operationId=record.id;error.jobId=record.job?.id;
    error.uncertain=!terminal&&(Boolean(record.job)||transient(error));
    error.recoverable=error.uncertain&&transient(error)&&!record.protocolError&&now()-record.created<horizon;
    record.error={message:error.message,status:error.status,uncertain:error.uncertain,recoverable:error.recoverable};record.blocked=!error.recoverable;
    if(!error.uncertain)record.final=true;
    emit(record,error.uncertain?'unavailable':'failed');return error;
  }
  async function read(record,path,deadline) {
    for(let attempt=0;;attempt++) {
      check(record);
      if(now()>=deadline)throw fault('Tracking paused at the observation limit. Continue tracking the same operation.',504);
      try {return await request(path,{signal:AbortSignal.timeout(Math.max(1,Math.min(30000,deadline-now())))});}
      catch(error) {
        if(!transient(error)||attempt>=retryDelays.length)throw error;
        emit(record,'reconnecting');await sleep(retryDelays[attempt]);
      }
    }
  }
  async function execute(record,path,body) {
    try {
      check(record);
      if(record.final)return record.job.result;
      const deadline=now()+maxWait;let response;
      if(!record.submitted) {
        record.submitted=true;emit(record,'submitting');
        response=await request(path,{method:'POST',body:{...body,operation_id:record.id}});
      } else {
        const lookup=record.job?'/jobs/'+encodeURIComponent(record.job.id):'/jobs/by-operation/'+encodeURIComponent(record.id);
        response=await read(record,lookup,deadline);
      }
      for(;;) {
        try {accept(record,response);}catch(error){if(error.status===502)record.protocolError=true;throw error;}
        if(record.final)return record.job.result;
        if(now()>=deadline)throw fault('Tracking paused at the observation limit. Continue tracking the same operation.',504);
        await sleep(pollInterval);
        response=await read(record,'/jobs/'+encodeURIComponent(record.job.id),deadline);
      }
    } catch(error) {throw annotate(record,error);}
  }
  function follow(record,notify,path,body) {
    try {check(record);}catch(error){return Promise.reject(annotate(record,error));}
    if(notify)record.observers.add(notify);
    if(record.job)emit(record,record.job.state);
    if(!record.flight) {
      // Defer dispatch until flight is assigned so simultaneous callers share it.
      record.flight=Promise.resolve().then(()=>execute(record,path,body)).finally(()=>{record.flight=null;});
    }
    return record.flight.finally(()=>{if(notify)record.observers.delete(notify);});
  }
  return {
    async run(path,body={},notify) {
      const snapshot=JSON.parse(JSON.stringify(body)),owner=actor();
      const supplied=snapshot.operation_id;delete snapshot.operation_id;
      if(supplied!==undefined&&(typeof supplied!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(supplied)))throw fault('Invalid operation identifier.',400);
      const key=await fingerprint(path,snapshot);
      if(owner!==actor())throw fault('The active account changed. Reopen the app.',403);
      let record=supplied?records.get(supplied):[...records.values()].find(r=>r.actor===owner&&!r.final&&r.key===key);
      if(record&&(record.actor!==owner||record.key!==key))throw fault('Operation ID was already used for a different request.',409);
      record ||= remember(make(supplied||crypto.randomUUID(),key));
      return follow(record,notify,path,snapshot);
    },
    resume(id,notify) {
      const record=records.get(id);
      if(!record)return Promise.reject(fault('Recovery details are unavailable. Open Operations to inspect the saved job.',410));
      return follow(record,notify);
    },
  };
}
