export type Session={authenticated:boolean;user:{user_id?:string;user_name?:string;user_shortname?:string;email?:string;broker?:string;exchanges?:string[];products?:string[];login_time?:string};expires_at:string};
export type MarketRow={instrument_token:number;symbol:string;name:string;exchange:string;instrument_type:string;expiry:string|null;last_price:number;close:number;open:number;high:number;low:number;volume:number;change:number;change_percent:number;exchange_timestamp:string|null};
export type MarketSnapshot={status:string;error:string|null;subscribed_instruments:number;received_instruments:number;breadth:{advances:number;declines:number;unchanged:number;total:number;advance_decline_ratio:number|null};equity:{gainers:MarketRow[];losers:MarketRow[]};futures:{gainers:MarketRow[];losers:MarketRow[]};all_equity:MarketRow[];all_futures:MarketRow[];indices:MarketRow[];updated_at:string|null};
async function call<T>(path:string,init:RequestInit={}):Promise<T>{
 const response=await fetch("/api"+path,{...init,credentials:"include",headers:{"Content-Type":"application/json",...init.headers}});
 if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||"Request failed. Please try again.");}
 return response.json() as Promise<T>;
}
export const api={
 login:()=>call<{login_url:string}>("/auth/zerodha/login"),
 session:()=>call<Session>("/auth/session"),
 logout:()=>call<{authenticated:boolean}>("/auth/logout",{method:"POST"}),
 marketSnapshot:()=>call<MarketSnapshot>("/market/snapshot")
};
