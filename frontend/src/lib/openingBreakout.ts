export type BreakoutTrade={id:string;symbol:string;direction:string;mode:string;status:string;entry_price?:number;exit_price?:number;exit_reason?:string;quantity?:number;opened_at?:string;closed_at?:string;pnl?:number;option_symbol?:string;stop_price?:number};
export type BreakoutConfig={gainers_count:number;losers_count:number;stop_loss_points:number;max_positions:number;max_spread_percent:number;exit_time:string};
export type BreakoutSettings={mode:"PAPER"|"LIVE";config:BreakoutConfig;live_ready:boolean;live_block_reason:string};
export type BreakoutStatus={running:boolean;mode:string|null;market_data_ready:boolean;watchlist_count:number;open_positions:number;trades_count:number;watchlist?:Array<{symbol:string;opening_price:number|null;last_price:number|null;signal:string|null;traded:boolean;skipped:boolean}>;positions?:Array<Record<string,unknown>>;events:Array<Record<string,unknown>>;live_ready:boolean;live_block_reason:string};
async function call<T>(path:string,init:RequestInit={}):Promise<T>{const r=await fetch("/api/strategies/opening-breakout"+path,{...init,credentials:"include",headers:{"Content-Type":"application/json",...init.headers}});if(!r.ok){const x=await r.json().catch(()=>({}));throw new Error(x.detail||"Strategy request failed")}return r.json() as Promise<T>}
export const api={
 status:()=>call<BreakoutStatus>("/status"),
 trades:()=>call<BreakoutTrade[]>("/trades"),
 settings:()=>call<BreakoutSettings>("/settings"),
 saveSettings:(config:BreakoutConfig)=>call<BreakoutSettings>("/settings",{method:"PUT",body:JSON.stringify(config)}),
 setMode:(mode:"PAPER"|"LIVE")=>call<BreakoutSettings>("/mode",{method:"PUT",body:JSON.stringify({mode})}),
 start:(mode:"PAPER"|"LIVE")=>call("/start",{method:"POST",body:JSON.stringify({mode,confirm_live:false})}),
 stop:()=>call("/stop",{method:"POST"})
};
