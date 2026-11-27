export type BreakoutTrade={id:string;symbol:string;direction:string;mode:string;status:string;entry_price?:number;exit_price?:number;exit_reason?:string;quantity?:number;opened_at?:string;closed_at?:string;pnl?:number;option_symbol?:string;stop_price?:number};
export type BreakoutConfig={auto_start:boolean;gainers_count:number;losers_count:number;stop_loss_points:number;max_positions:number;max_spread_percent:number;exit_time:string};
export type BreakoutSettings={mode:"PAPER"|"LIVE";config:BreakoutConfig;live_ready:boolean;live_block_reason:string};
export type BreakoutStatus={running:boolean;mode:string|null;market_data_ready:boolean;watchlist_count:number;open_positions:number;trades_count:number;watchlist?:Array<{symbol:string;opening_price:number|null;last_price:number|null;signal:string|null;signal_stage?:string;traded:boolean;skipped:boolean}>;positions?:Array<Record<string,unknown>>;events:Array<Record<string,unknown>>;live_ready:boolean;live_block_reason:string};
function responseError(detail:unknown):string{
 if(typeof detail==="string")return detail;
 if(Array.isArray(detail)){
  return detail.map((item:any)=>{
   const location=Array.isArray(item?.loc)?item.loc.filter((part:any)=>part!=="body").join(" → "):"";
   const message=typeof item?.msg==="string"?item.msg:"Invalid value";
   return location?`${location}: ${message}`:message;
  }).join("; ");
 }
 if(detail&&typeof detail==="object"&&"msg" in detail)return String((detail as {msg:unknown}).msg);
 return "Strategy request failed. Please check the entered values.";
}
async function call<T>(path:string,init:RequestInit={}):Promise<T>{const r=await fetch("/api/strategies/opening-breakout"+path,{...init,credentials:"include",headers:{"Content-Type":"application/json",...init.headers}});if(!r.ok){const x=await r.json().catch(()=>({}));throw new Error(responseError(x.detail))}return r.json() as Promise<T>}
export const api={
 status:()=>call<BreakoutStatus>("/status"),
 trades:(date?:string)=>call<BreakoutTrade[]>(date?`/trades?date=${encodeURIComponent(date)}`:"/trades"),
 settings:()=>call<BreakoutSettings>("/settings"),
 saveSettings:(config:BreakoutConfig)=>call<BreakoutSettings>("/settings",{method:"PUT",body:JSON.stringify(config)}),
 setMode:(mode:"PAPER"|"LIVE")=>call<BreakoutSettings>("/mode",{method:"PUT",body:JSON.stringify({mode})}),
 start:(mode:"PAPER"|"LIVE")=>call("/start",{method:"POST",body:JSON.stringify({mode,confirm_live:false})}),
 deleteTrade:(id:string)=>call<{deleted:boolean;id:string}>(`/trades/${encodeURIComponent(id)}`,{method:"DELETE"}),
 stop:()=>call("/stop",{method:"POST"})
};
