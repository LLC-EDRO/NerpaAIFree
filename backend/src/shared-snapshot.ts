/** Immutable system resources may be shared; uploaded document state may not.
 * A changed fingerprint gets a new snapshot. Active users retain the old one. */
export function createSharedSnapshot<T>(dispose:(value:T)=>Promise<void>, idleMs=15*60_000) {
  const entries=new Map<string,{value:Promise<T>;users:number;timer?:ReturnType<typeof setTimeout>}>();
  return async (key:string,build:()=>Promise<T>)=>{
    let entry=entries.get(key);
    if(!entry){
      entry={value:Promise.resolve().then(build),users:0};
      entries.set(key,entry);
      void entry.value.catch(()=>{if(entries.get(key)===entry)entries.delete(key);});
    }
    const current=entry;current.users++;if(current.timer)clearTimeout(current.timer);
    let released=false;
    const release=()=>{
      if(released)return;released=true;current.users--;
      if(current.users)return;
      current.timer=setTimeout(()=>{
        if(current.users || entries.get(key)!==current)return;
        entries.delete(key);void current.value.then(dispose).catch(()=>{});
      },idleMs);current.timer.unref();
    };
    try{return {value:await current.value,release};}catch(error){release();throw error;}
  };
}
