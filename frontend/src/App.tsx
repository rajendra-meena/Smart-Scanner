import {useCallback,useEffect,useMemo,useRef,useState} from "react";
import {Activity,ArrowUpRight,LogOut,RefreshCw,Search,ShieldCheck} from "lucide-react";
import {api,type MarketRow,type MarketSnapshot,type MarketUniverses,type Session} from "./lib/api";
import OpeningBreakout from "./components/OpeningBreakout";

type UniverseKey="NIFTY_50"|"BANK_NIFTY"|"NIFTY_NEXT_50"|"ABOVE_20"|"FNO"|"ALL";
const UNIVERSE_OPTIONS:{value:UniverseKey;label:string}[]=[{value:"NIFTY_50",label:"NIFTY 50"},{value:"BANK_NIFTY",label:"BANK NIFTY"},{value:"NIFTY_NEXT_50",label:"NIFTY NEXT 50"},{value:"ABOVE_20",label:"Securities > Rs 20"},{value:"FNO",label:"F&O Securities"},{value:"ALL",label:"All Securities"}];
const money=(v:number)=>new Intl.NumberFormat("en-IN",{maximumFractionDigits:2}).format(v);
const pct=(v:number)=>`${v>0?"+":""}${v.toFixed(2)}%`;
const volumeText=(v:number)=>v>=10000000?`${(v/10000000).toFixed(2)} Cr`:v>=100000?`${(v/100000).toFixed(2)} L`:new Intl.NumberFormat("en-IN",{maximumFractionDigits:0}).format(v);

export default function App(){
 const [session,setSession]=useState<Session|null>(null);
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const [universe,setUniverse]=useState<UniverseKey>("NIFTY_50");
 const [universes,setUniverses]=useState<MarketUniverses|null>(null),[universeLoadError,setUniverseLoadError]=useState("");
 const [snapshot,setSnapshot]=useState<MarketSnapshot|null>(null),[stream,setStream]=useState("disconnected");
 const [search,setSearch]=useState(""),[showAll,setShowAll]=useState(false);
 const [page,setPage]=useState<"market"|"strategy">("market");
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
       const rows=[...map.values()],equity=rows.filter(r=>r.instrument_type==="EQ"),futures=rows.filter(r=>r.instrument_type==="FUT"),indices=rows.filter(r=>r.instrument_type==="INDICES"||r.exchange==="NSE"&&r.symbol?.startsWith("NIFTY"));
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
  void start();return()=>{active=false;socket.current?.close();socket.current=null};
 },[session]);
 const connect=async()=>{setBusy(true);setError("");try{location.assign((await api.login()).login_url)}catch(e){setError(e instanceof Error?e.message:"Unable to start login.");setBusy(false)}};
 const logout=async()=>{setBusy(true);setError("");socket.current?.close();try{await api.logout();setSession(null);setSnapshot(null);setStream("disconnected")}catch(e){setError(e instanceof Error?e.message:"Logout failed.")}finally{setBusy(false)}};
 const rows=useMemo(()=>{
  if(!snapshot)return[];const equity=snapshot.all_equity.filter(row=>row.open>0&&row.high>0&&row.low>0);
  if(universe==="ABOVE_20")return equity.filter(row=>row.last_price>20);
  if(universe==="FNO"){const symbols=new Set(universes?.fno_symbols||[]);return equity.filter(row=>symbols.has(row.symbol.toUpperCase()))}
  if(universe==="ALL")return equity;const constituents=universes?.constituents[universe];if(!constituents)return[];const symbols=new Set(constituents);return equity.filter(row=>symbols.has(row.symbol.toUpperCase()));
 },[snapshot,universe,universes]);
 const searched=rows.filter(r=>r.symbol.toLowerCase().includes(search.toLowerCase())||r.name.toLowerCase().includes(search.toLowerCase()));
 const rankedGainers=searched.filter(r=>r.change_percent>0).sort((a,b)=>b.change_percent-a.change_percent),rankedLosers=searched.filter(r=>r.change_percent<0).sort((a,b)=>a.change_percent-b.change_percent);
 const connected=stream==="connected"||snapshot?.status==="connected",tickAgeSeconds=snapshot?.last_tick_received_at?Math.max(0,Math.floor((Date.now()-new Date(snapshot.last_tick_received_at).getTime())/1000)):null,freshTicks=tickAgeSeconds!==null&&tickAgeSeconds<30;
 return <div className="app-shell"><main className="main"><header className="topbar"><div className="topbar-brand"><span className="topbar-mark"><Activity size={18}/></span><span className="topbar-name">smart<span>scanner</span><small>MARKET INTELLIGENCE</small></span><span className="topbar-divider"/><div className="crumb">Workspace <span>/</span> <b>{page==="market"?"Gainers / Losers":"Opening Breakout"}</b></div></div><div className="top-actions">{session?<><span className={connected?"connection live":"connection"}><i/>{freshTicks?"LIVE TICKS":connected?"SOCKET CONNECTED":stream.toUpperCase()}</span><span className="profile-pill">{session.user.user_shortname||session.user.user_name||session.user.user_id}</span><button className="icon-button" onClick={logout} disabled={busy} title="Logout"><LogOut size={16}/></button></>:<span className="connection">NOT CONNECTED</span>}</div></header>
 <div className="page"><div className="page-heading"><div><div className="eyebrow">YOUR MARKET, IN FOCUS</div><h1>{page==="market"?"Gainers & losers":"Opening Price Breakout"}<span>.</span></h1><p>{page==="market"?"Live NSE market movers from Zerodha Kite Connect.":"Automated opening range strategy · Paper and Live modes."}</p></div><div className="heading-right"><span className={connected?"live-dot":"muted-dot"}/>{freshTicks?"Receiving live ticks":connected?"Connected · waiting for ticks":"Waiting for feed"}</div></div>
 {session&&<nav className="tabs"><button className={page==="market"?"tab active":"tab"} onClick={()=>setPage("market")}>Gainers / Losers</button><button className={page==="strategy"?"tab active":"tab"} onClick={()=>setPage("strategy")}>Opening Breakout</button></nav>}
 {error&&<div className="alert">{error}</div>}
 {!session?<section className="connect-panel"><ShieldCheck size={28}/><h2>Connect your Zerodha account</h2><p>Authenticate securely to start the live market data stream.</p><button className="primary" onClick={connect} disabled={busy||loading}>{busy?"Connecting...":"Connect to Zerodha"} <ArrowUpRight size={16}/></button></section>:page==="strategy"?<OpeningBreakout/>:<>
 <div className="status-strip"><div><span className={connected?"live-dot":"muted-dot"}/><b>{connected?"Market feed connected":"Market feed "+stream}</b><small>{snapshot?.received_instruments||0} instruments with ticks · {snapshot?.subscribed_instruments||0} subscribed</small></div><button className="subtle-button" onClick={()=>void api.marketSnapshot().then(setSnapshot).catch(e=>setError(e.message))}><RefreshCw size={14}/> Refresh snapshot</button></div>
 <MarketOverview snapshot={snapshot}/>
 <section className="panel"><div className="panel-title"><div><h2>Gainers & losers</h2><p>Live NSE equity ranking · percentage change from previous close</p></div><span className="live-label">{rows.length} MATCHED SECURITIES</span></div>
 <div className="universe-toolbar"><label className="universe-select"><span>Select Index</span><select value={universe} onChange={e=>{setUniverse(e.target.value as UniverseKey);setShowAll(false)}}>{UNIVERSE_OPTIONS.map(option=><option value={option.value} key={option.value}>{option.label}</option>)}</select></label><label className="search"><Search size={15}/><input placeholder="Search symbol" value={search} onChange={e=>setSearch(e.target.value)}/></label></div>
 {!universes&&<div className="empty">{universeLoadError? `Unable to load official NSE constituent lists: ${universeLoadError}`:"Loading official NSE constituent lists…"}</div>}
 {universes&&<><div className="mover-stack"><MoverList title="Top 20 gainers" rows={rankedGainers.slice(0,showAll?50:20)} positive/><MoverList title="Top 20 losers" rows={rankedLosers.slice(0,showAll?50:20)}/></div><button className="show-more" onClick={()=>setShowAll(v=>!v)}>{showAll?"Show top 20":"Show more"}</button><MarketHeatmap rows={[...rankedGainers.slice(0,showAll?50:20),...rankedLosers.slice(0,showAll?50:20)]}/></>}
 </section></>}
 <footer className="footer"><span>SMART SCANNER · MARKET INTELLIGENCE</span><span><ShieldCheck size={12}/> PRIVATE SESSION · PAPER FIRST · LIVE SAFETY GATED</span></footer>
 </div></main></div>;
}
const SECTOR_INDEX_NAMES=["NIFTY AUTO","NIFTY FMCG","NIFTY IT","NIFTY PHARMA","NIFTY METAL","NIFTY REALTY","NIFTY PSU BANK","NIFTY PRIVATE BANK","NIFTY ENERGY","NIFTY MEDIA","NIFTY HEALTHCARE","NIFTY OIL AND GAS","NIFTY CONSUMER DURABLES","NIFTY FIN SERVICE"];
const CORE_INDEX_NAMES=["NIFTY 50","NIFTY BANK","NIFTY FIN SERVICE","NIFTY NEXT 50","NIFTY MIDCAP 50","NIFTY 100","NIFTY 200","NIFTY 500"];
function MarketOverview({snapshot}:{snapshot:MarketSnapshot|null}){
 const allIndices=snapshot?.indices||[];
 const sectors=allIndices.filter(row=>SECTOR_INDEX_NAMES.includes(row.symbol.toUpperCase())).sort((a,b)=>b.change_percent-a.change_percent);
 const indices=allIndices.filter(row=>CORE_INDEX_NAMES.includes(row.symbol.toUpperCase()));
 const advancing=sectors.filter(row=>row.change_percent>0).length;
 const declining=sectors.filter(row=>row.change_percent<0).length;
 const unchanged=sectors.filter(row=>row.change_percent===0).length;
 const breadth=snapshot?.breadth;
 return <section className="market-overview">
  <div className="overview-topline"><div><span className="heatmap-kicker">MARKET PULSE</span><h2>Market breadth &amp; indices</h2><p>Live values from subscribed Zerodha index instruments</p></div><span className={snapshot?.status==="connected"?"overview-live":"overview-wait"}><i/> {snapshot?.status==="connected"?"LIVE INDEX FEED":"WAITING FOR INDEX TICKS"}</span></div>
  <div className="breadth-layout">
   <div className="breadth-card"><div className="overview-card-label">NSE EQUITY BREADTH</div><div className="breadth-counts"><div><strong className="up">{breadth?.advances??"—"}</strong><small>Advances</small></div><div><strong className="down">{breadth?.declines??"—"}</strong><small>Declines</small></div><div><strong>{breadth?.unchanged??"—"}</strong><small>Unchanged</small></div></div><div className="breadth-track"><span style={{width:`${breadth?.total?breadth.advances/breadth.total*100:0}%`}}/><i style={{width:`${breadth?.total?breadth.unchanged/breadth.total*100:0}%`}}/></div><div className="breadth-footer"><span>{breadth?.total??0} stocks with valid ticks</span><span>A/D {breadth?.advance_decline_ratio??"—"}</span></div></div>
   <div className="sector-card"><div className="overview-card-heading"><div><div className="overview-card-label">SECTOR BREADTH</div><strong>{sectors.length?(`${advancing} advancing · ${declining} declining`):"Waiting for sector ticks"}</strong></div><span>{sectors.length} sectors</span></div><div className="sector-grid">{sectors.length?sectors.map(row=><div className={row.change_percent>0?"sector-chip positive":row.change_percent<0?"sector-chip negative":"sector-chip flat"} key={row.instrument_token}><span>{row.symbol.replace("NIFTY ","")}</span><b>{pct(row.change_percent)}</b></div>):<div className="overview-empty">Sector index ticks will appear when Zerodha sends live data.</div>}</div><div className="breadth-footer"><span>{advancing} up</span><span>{declining} down</span><span>{unchanged} unchanged</span></div></div>
  </div>
  <div className="indices-section"><div className="indices-heading"><div className="overview-card-label">KEY INDICES</div><span>Live index levels · day change</span></div><div className="indices-grid">{indices.length?indices.map(row=><div className="index-tile" key={row.instrument_token}><div><span>{row.symbol}</span><i className={row.change_percent>=0?"index-up":"index-down"}>{row.change_percent>=0?"▲":"▼"}</i></div><strong>{money(row.last_price)}</strong><small className={row.change_percent>=0?"up":"down"}>{pct(row.change_percent)}</small></div>):<div className="overview-empty">Waiting for key index ticks from Zerodha.</div>}</div></div>
 </section>;
}

function MarketHeatmap({rows}:{rows:MarketRow[]}){
 const ordered=[...rows].sort((a,b)=>b.change_percent-a.change_percent);
 const maxMove=Math.max(1,...ordered.map(row=>Math.abs(row.change_percent)));
 return <section className="market-heatmap">
  <div className="heatmap-heading"><div><span className="heatmap-kicker">LIVE MARKET VISUALIZATION</span><h3>Gainers &amp; losers heatmap</h3><p>Real-time price change from previous close · Current selected universe</p></div><div className="heatmap-legend"><span><i className="heat-positive"/> Gainers</span><span><i className="heat-negative"/> Losers</span></div></div>
  {ordered.length?<div className="heatmap-grid">{ordered.map(row=>{
   const positive=row.change_percent>=0;
   const intensity=Math.min(1,Math.abs(row.change_percent)/maxMove);
   const alpha=(0.16+intensity*0.42).toFixed(2);
   const background=positive?`rgba(104, 115, 71, ${alpha})`:`rgba(174, 91, 84, ${alpha})`;
   return <div className="heat-tile" key={row.instrument_token} style={{backgroundColor:background}} title={`${row.symbol}: ${pct(row.change_percent)} · LTP ₹${money(row.last_price)}`}>
    <div className="heat-tile-top"><b>{row.symbol}</b><span>{positive?"▲":"▼"}</span></div>
    <strong className={positive?"up":"down"}>{pct(row.change_percent)}</strong>
    <small>₹{money(row.last_price)}</small>
   </div>;
  })}</div>:<div className="empty">No live gainers or losers are available for this selection.</div>}
  <div className="heatmap-footnote"><span>{ordered.length} securities</span><span>Tile intensity reflects percentage move within this heatmap</span></div>
 </section>;
}
function MoverList({title,rows,positive=false}:{title:string;rows:MarketRow[];positive?:boolean}){
 return <div className="mover-card mover-table-card"><div className="mover-title"><h3>{title}</h3><span>{rows.length} instruments</span></div>{rows.length?<div className="mover-table-wrap"><table className="mover-table"><thead><tr><th>SYMBOL</th><th>OPEN</th><th>HIGH</th><th>LOW</th><th>PREV. CLOSE</th><th>LTP</th><th>% CHNG</th></tr></thead><tbody>{rows.map(row=><tr key={row.instrument_token}><td className="symbol-cell">{row.symbol}</td><td>{money(row.open)}</td><td>{money(row.high)}</td><td>{money(row.low)}</td><td>{money(row.close)}</td><td className="ltp-cell">{money(row.last_price)}</td><td className={row.change_percent>=0?"up":"down"}>{pct(row.change_percent)}</td></tr>)}</tbody></table></div>:<div className="empty">No valid live market rows available for this selection.</div>}</div>;
}
