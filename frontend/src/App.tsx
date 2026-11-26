import {useCallback,useEffect,useMemo,useRef,useState} from "react";
import {Activity,ArrowDownRight,ArrowUpRight,BarChart3,Check,ChevronDown,CircleHelp,LogOut,RefreshCw,Search,ShieldCheck,Signal,Wifi,WifiOff} from "lucide-react";
import {api,type MarketRow,type MarketSnapshot,type MarketUniverses,type Session} from "./lib/api";

type Tab="breadth"|"gainers"|"contributors";
type UniverseKey="NIFTY_50"|"BANK_NIFTY"|"NIFTY_NEXT_50"|"ABOVE_20"|"FNO"|"ALL";
const UNIVERSE_OPTIONS:{value:UniverseKey;label:string}[]=[{value:"NIFTY_50",label:"NIFTY 50"},{value:"BANK_NIFTY",label:"BANK NIFTY"},{value:"NIFTY_NEXT_50",label:"NIFTY NEXT 50"},{value:"ABOVE_20",label:"Securities > Rs 20"},{value:"FNO",label:"F&O Securities"},{value:"ALL",label:"All Securities"}];
const money=(v:number)=>new Intl.NumberFormat("en-IN",{maximumFractionDigits:2}).format(v);
const pct=(v:number)=>`${v>0?"+":""}${v.toFixed(2)}%`;
const volumeText=(v:number)=>v>=10000000?`${(v/10000000).toFixed(2)} Cr`:v>=100000?`${(v/100000).toFixed(2)} L`:new Intl.NumberFormat("en-IN",{maximumFractionDigits:0}).format(v);

export default function App(){
 const [session,setSession]=useState<Session|null>(null);
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const [tab,setTab]=useState<Tab>("breadth"),[universe,setUniverse]=useState<UniverseKey>("NIFTY_50");
 const [universes,setUniverses]=useState<MarketUniverses|null>(null),[universeLoadError,setUniverseLoadError]=useState("");
 const [snapshot,setSnapshot]=useState<MarketSnapshot|null>(null),[stream,setStream]=useState("disconnected");
 const [search,setSearch]=useState(""),[showAll,setShowAll]=useState(false);
 const socket=useRef<WebSocket|null>(null);

 const refreshSession=useCallback(async()=>{try{const value=await api.session();setSession(value)}catch{setSession(null)}finally{setLoading(false)}},[]);
 useEffect(()=>{const params=new URLSearchParams(location.search);if(params.get("auth")==="failed")setError("Zerodha login could not be completed. Please try again.");if(params.has("auth"))history.replaceState({},document.title,location.pathname);void refreshSession()},[refreshSession]);

 useEffect(()=>{
  if(!session)return;
  let active=true;
  const start=async()=>{
   try{
    const initial=await api.marketSnapshot();if(!active)return;setSnapshot(initial);
    try{const officialUniverses=await api.marketUniverses();if(!active)return;setUniverses(officialUniverses);setUniverseLoadError("");}catch(e){if(!active)return;setUniverseLoadError(e instanceof Error?e.message:"Official NSE constituent lists unavailable.");}
    const protocol=location.protocol==="https:"?"wss:":"ws:";
    const ws=new WebSocket(`${protocol}//${location.host}/api/market/stream`);
    socket.current=ws;setStream("connecting");
    ws.onopen=()=>{if(active)setStream("connected")};
    ws.onmessage=(event)=>{
     if(!active)return;
     try{
      const message=JSON.parse(event.data);
      if(message.type==="snapshot"){setSnapshot(message.data);setStream(message.data.status)}
      if(message.type==="status")setStream(message.status);
      if(message.type==="ticks")setSnapshot(prev=>{
       if(!prev)return prev;
       const map=new Map<number,MarketRow>();
       [...prev.all_equity,...prev.all_futures,...prev.indices].forEach(row=>map.set(row.instrument_token,row));
       message.data.forEach((row:MarketRow)=>map.set(row.instrument_token,row));
       const rows=[...map.values()];
       const equity=rows.filter(r=>r.instrument_type==="EQ");
       const futures=rows.filter(r=>r.instrument_type==="FUT");
       const indices=rows.filter(r=>r.instrument_type==="INDICES"||r.exchange==="NSE"&&r.symbol?.startsWith("NIFTY"));
       const ranked=(items:MarketRow[],positive:boolean)=>items.filter(r=>r.close>0).sort((a,b)=>positive?b.change_percent-a.change_percent:a.change_percent-b.change_percent);
       const advances=equity.filter(r=>r.change>0).length,declines=equity.filter(r=>r.change<0).length,unchanged=equity.filter(r=>r.change===0).length;
       return {...prev,status:"connected",received_instruments:rows.length,breadth:message.breadth||{advances,declines,unchanged,total:equity.length,advance_decline_ratio:declines?Number((advances/declines).toFixed(2)):null},all_equity:equity,all_futures:futures,equity:{gainers:ranked(equity,true).slice(0,50),losers:ranked(equity,false).slice(0,50)},futures:{gainers:ranked(futures,true).slice(0,50),losers:ranked(futures,false).slice(0,50)},indices};
      });
     }catch{setError("Market stream message could not be processed.")}
    };
    ws.onerror=()=>{if(active){setStream("error");setError("Market WebSocket connection failed. Check backend logs.")}};
    ws.onclose=()=>{if(active)setStream("disconnected")};
   }catch(e){if(active)setError(e instanceof Error?e.message:"Unable to start market data.");}
  };
  void start();
  return()=>{active=false;socket.current?.close();socket.current=null};
 },[session]);

 const connect=async()=>{setBusy(true);setError("");try{location.assign((await api.login()).login_url)}catch(e){setError(e instanceof Error?e.message:"Unable to start login.");setBusy(false)}};
 const logout=async()=>{setBusy(true);setError("");socket.current?.close();try{await api.logout();setSession(null);setSnapshot(null);setStream("disconnected")}catch(e){setError(e instanceof Error?e.message:"Logout failed.")}finally{setBusy(false)}};
 const rows=useMemo(()=>{
  if(!snapshot)return[];
  const equity=snapshot.all_equity;
  if(universe==="ABOVE_20")return equity.filter(row=>row.last_price>20);
  if(universe==="FNO"){
   const symbols=new Set(universes?.fno_symbols||[]);
   return equity.filter(row=>symbols.has(row.symbol.toUpperCase()));
  }
  if(universe==="ALL")return equity;
  const constituents=universes?.constituents[universe];
  if(!constituents)return[];
  const symbols=new Set(constituents);
  return equity.filter(row=>symbols.has(row.symbol.toUpperCase()));
 },[snapshot,universe,universes]);
 const searched=rows.filter(r=>r.symbol.toLowerCase().includes(search.toLowerCase())||r.name.toLowerCase().includes(search.toLowerCase()));
 const rankedGainers=searched.filter(r=>r.change_percent>0).sort((a,b)=>b.change_percent-a.change_percent);
 const rankedLosers=searched.filter(r=>r.change_percent<0).sort((a,b)=>a.change_percent-b.change_percent);
 const connected=stream==="connected"||snapshot?.status==="connected";
 const tickAgeSeconds=snapshot?.last_tick_received_at?Math.max(0,Math.floor((Date.now()-new Date(snapshot.last_tick_received_at).getTime())/1000)):null;
 const freshTicks=tickAgeSeconds!==null&&tickAgeSeconds<30;
 const tickTime=snapshot?.last_tick_received_at?new Date(snapshot.last_tick_received_at).toLocaleTimeString("en-IN",{timeZone:"Asia/Kolkata"}):null;
 const breadth=snapshot?.breadth;
 const maxAbs=Math.max(1,breadth?.advances||0,breadth?.declines||0);
 return <div className="app-shell">
  <aside className="side"><div className="brand"><span className="brand-mark"><Activity size={20}/></span><span>smart<span className="brand-accent">scanner</span><small>MARKET INTELLIGENCE</small></span></div><div className="nav-caption">MARKET WORKSPACE</div>
   <button className={tab==="breadth"?"side-link selected":"side-link"} onClick={()=>setTab("breadth")}><BarChart3 size={17}/>Advance / Decline</button>
   <button className={tab==="gainers"?"side-link selected":"side-link"} onClick={()=>setTab("gainers")}><ArrowUpRight size={17}/>Gainers / Losers</button>
   <button className={tab==="contributors"?"side-link selected":"side-link"} onClick={()=>setTab("contributors")}><Activity size={17}/>Index Contributors</button>
   <div className="side-bottom"><div className="security-note"><ShieldCheck size={18}/><b>Secure by design</b><p>Market data is streamed through your authenticated backend session.</p></div><span className="version">SMART SCANNER · 0.2.0</span></div>
  </aside>
  <main className="main"><header className="topbar"><div className="crumb">Workspace <span>/</span> <b>Market overview</b></div><div className="top-actions">{session?<><span className={connected?"connection live":"connection"}><i/>{freshTicks?"LIVE TICKS":connected?"SOCKET CONNECTED":stream.toUpperCase()}</span><span className="profile-pill">{session.user.user_shortname||session.user.user_name||session.user.user_id}</span><button className="icon-button" onClick={logout} disabled={busy} title="Logout"><LogOut size={16}/></button></>:<span className="connection">NOT CONNECTED</span>}</div></header>
  <div className="page">
   <div className="page-heading"><div><div className="eyebrow">YOUR MARKET, IN FOCUS</div><h1>Market overview<span>.</span></h1><p>Live market breadth and momentum from Zerodha Kite Connect.</p></div><div className="heading-right"><span className={connected?"live-dot":"muted-dot"}/>{freshTicks?"Receiving live ticks":connected?"Connected · waiting for ticks":"Waiting for feed"}</div></div>
   {error&&<div className="alert">{error}</div>}
   {!session?<section className="connect-panel"><ShieldCheck size={28}/><h2>Connect your Zerodha account</h2><p>Authenticate securely to start the live market data stream.</p><button className="primary" onClick={connect} disabled={busy||loading}>{busy?"Connecting...":"Connect to Zerodha"} <ArrowUpRight size={16}/></button></section>:<>
    <div className="status-strip"><div><span className={connected?"live-dot":"muted-dot"}/><b>{connected?"Market feed connected":"Market feed "+stream}</b><small>{snapshot?.received_instruments||0} instruments with ticks · {snapshot?.subscribed_instruments||0} subscribed</small></div><button className="subtle-button" onClick={()=>void api.marketSnapshot().then(setSnapshot).catch(e=>setError(e.message))}><RefreshCw size={14}/> Refresh snapshot</button></div>
    <div className="tabs">{(["breadth","gainers","contributors"] as Tab[]).map(t=><button key={t} className={tab===t?"tab active":"tab"} onClick={()=>{setTab(t);setShowAll(false)}}>{t==="breadth"?"Advance Decline":t==="gainers"?"Gainer Loser":"Index Contributors"}</button>)}</div>
    {tab==="breadth"&&<section className="panel">
      <div className="panel-title"><div><h2>Market breadth</h2><p>NSE cash equities with live ticks received in this session</p></div><span className="live-label">LIVE UNIVERSE</span></div>
      <div className="breadth-cards"><div className="metric positive"><small>ADVANCES</small><strong>{breadth?.advances??"—"}</strong><span>Stocks trading higher</span></div><div className="metric negative"><small>DECLINES</small><strong>{breadth?.declines??"—"}</strong><span>Stocks trading lower</span></div><div className="metric neutral"><small>UNCHANGED</small><strong>{breadth?.unchanged??"—"}</strong><span>No price change</span></div><div className="metric neutral"><small>ADV / DEC RATIO</small><strong>{breadth?.advance_decline_ratio??"—"}</strong><span>{breadth?.total??0} instruments received</span></div></div>
      <div className="breadth-bar"><div className="bar-label"><b>Advances</b><span>{breadth?.advances??0}</span></div><div className="track"><i className="up-fill" style={{width:`${breadth?breadth.advances/Math.max(1,breadth.total)*100:0}%`}}/></div><div className="bar-label"><b>Declines</b><span>{breadth?.declines??0}</span></div><div className="track"><i className="down-fill" style={{width:`${breadth?breadth.declines/Math.max(1,breadth.total)*100:0}%`}}/></div></div>
      <div className="section-head"><div><h3>Market movers</h3><p>Top 10 gainers and losers from received NSE equity ticks</p></div></div><div className="two-col"><MoverList title="Top gainers" rows={snapshot?.equity.gainers.slice(0,10)||[]} positive/><MoverList title="Top losers" rows={snapshot?.equity.losers.slice(0,10)||[]}/></div>
     </section>}
    {tab==="gainers"&&<section className="panel"><div className="panel-title"><div><h2>Gainers & losers</h2><p>Live NSE equity ranking · percentage change from previous close</p></div><span className="live-label">{rows.length} MATCHED SECURITIES</span></div>
      <div className="universe-toolbar"><label className="universe-select"><span>Select Index</span><select value={universe} onChange={e=>{setUniverse(e.target.value as UniverseKey);setShowAll(false)}}>{UNIVERSE_OPTIONS.map(option=><option value={option.value} key={option.value}>{option.label}</option>)}</select></label><label className="search"><Search size={15}/><input placeholder="Search symbol" value={search} onChange={e=>setSearch(e.target.value)}/></label></div>
      {!universes&&<div className="empty">{universeLoadError?`Unable to load official NSE constituent lists: ${universeLoadError}`:"Loading official NSE constituent lists…"}</div>}
      {universes&&<><div className="mover-stack"><MoverList title="Top 20 gainers" rows={rankedGainers.slice(0,showAll?50:20)} positive/><MoverList title="Top 20 losers" rows={rankedLosers.slice(0,showAll?50:20)}/></div><button className="show-more" onClick={()=>setShowAll(v=>!v)}>{showAll?"Show top 20":"Show more"}</button></>}
     </section>}
    {tab==="contributors"&&<section className="panel"><div className="panel-title"><div><h2>Index contributors</h2><p>Live index quotes are available; constituent-weighted points require an official index weight feed.</p></div></div>
      <div className="index-grid">{(snapshot?.indices||[]).map(i=><div className="index-card" key={i.instrument_token}><b>{i.symbol}</b><strong>{money(i.last_price)}</strong><span className={i.change>=0?"up":"down"}>{pct(i.change_percent)}</span></div>)}</div>
      <div className="info-panel"><CircleHelp size={20}/><div><b>Contribution points are not being estimated</b><p>Kite Connect streams index and constituent prices, but its instrument master does not provide index constituent weights. We will add verified official index constituents and weights before calculating positive/negative contribution points. This avoids showing fabricated contribution rankings.</p></div></div>
     </section>}
   </>}
   <footer className="footer"><span>SMART SCANNER · MARKET DATA</span><span><ShieldCheck size={12}/> PRIVATE SESSION · NO ORDERS PLACED</span></footer>
  </div></main>
 </div>;
}

function MoverList({title,rows,positive=false}:{title:string;rows:MarketRow[];positive?:boolean}){
 return <div className="mover-card mover-table-card"><div className="mover-title"><h3>{title}</h3><span>{rows.length} instruments</span></div>{rows.length?<div className="mover-table-wrap"><table className="mover-table"><thead><tr><th>SYMBOL</th><th>OPEN</th><th>HIGH</th><th>LOW</th><th>PREV. CLOSE</th><th>LTP</th><th>% CHNG</th><th>VOLUME</th></tr></thead><tbody>{rows.map(row=><tr key={row.instrument_token}><td className="symbol-cell">{row.symbol}</td><td>{money(row.open)}</td><td>{money(row.high)}</td><td>{money(row.low)}</td><td>{money(row.close)}</td><td className="ltp-cell">{money(row.last_price)}</td><td className={row.change_percent>=0?"up":"down"}>{pct(row.change_percent)}</td><td>{volumeText(row.volume)}</td></tr>)}</tbody></table></div>:<div className="empty">No valid live market rows available for this selection.</div>}</div>;
}
