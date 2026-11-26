export type Session={authenticated:boolean;user:{user_id?:string;user_name?:string;user_shortname?:string;email?:string;broker?:string;exchanges?:string[];products?:string[];login_time?:string};expires_at:string};
async function call<T>(path:string,init:RequestInit={}):Promise<T>{
 const response=await fetch("/api"+path,{...init,credentials:"include",headers:{"Content-Type":"application/json",...init.headers}});
 if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||"Request failed. Please try again.");}
 return response.json() as Promise<T>;
}
export const api={login:()=>call<{login_url:string}>("/auth/zerodha/login"),session:()=>call<Session>("/auth/session"),logout:()=>call<{authenticated:boolean}>("/auth/logout",{method:"POST"})};
