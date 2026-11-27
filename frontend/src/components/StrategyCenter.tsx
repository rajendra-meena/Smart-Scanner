import {useEffect,useState} from "react";
import {ArrowRight,CheckCircle2,PauseCircle,PlayCircle,Settings2,ShieldCheck} from "lucide-react";
import {api,type BreakoutSettings,type BreakoutStatus,type BreakoutTrade,type PlatformDefaults} from "../lib/openingBreakout";

const today=()=>{const now=new Date();return new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,10)};
const money=(v:unknown)=>typeof v==="number"?new Intl.NumberFormat("en-IN",{maximumFractionDigits:2}).format(v):"—";
const time=(v:unknown)=>typeof v==="string"&&v?new Date(v).toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit",second:"2-digit"}):"—";

export default function StrategyCenter({onOpenBreakout,onOpenPriceAction,onOpenBreakfast}:{onOpenBreakout:()=>void;onOpenPriceAction:()=>void;onOpenBreakfast:()=>void}){
 const [tab,setTab]=useState<"strategies"|"orders"|"defaults">("strategies");
 const [settings,setSettings]=useState<BreakoutSettings|null>(null);
 const [status,setStatus]=useState<BreakoutStatus|null>(null);
 const [trades,setTrades]=useState<BreakoutTrade[]>([]);
 const [tradeDate,setTradeDate]=useState(today());
 const [defaults,setDefaults]=useState<PlatformDefaults|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
 const refresh=async()=>{try{const [s,st,t,d]=await Promise.all([api.settings(),api.status(),api.trades(tradeDate),api.platformDefaults()]);setSettings(s);setStatus(st);setTrades(t);setDefaults(d)}catch(e){setError(e instanceof Error?e.message:"Unable to load strategy workspace")}};
 useEffect(()=>{void refresh();const timer=window.setInterval(()=>void refresh(),3000);return()=>window.clearInterval(timer)},[tradeDate]);
 const toggleStrategy=async()=>{if(!settings||status?.running)return;setBusy(true);setError("");setNotice("");try{const result=await api.saveSettings({...settings.config,enabled:!settings.config.enabled});setSettings(result);setNotice(result.config.enabled?"Opening Breakout enabled.":"Opening Breakout disabled; it will not auto-start or accept manual start.")}catch(e){setError(e instanceof Error?e.message:"Unable to update strategy")}finally{setBusy(false)}};
 const saveDefaults=async()=>{if(!defaults)return;setBusy(true);setError("");setNotice("");try{const result=await api.savePlatformDefaults(defaults);setDefaults(result);setNotice("Global defaults saved. These are defaults for newly added strategies; existing strategy settings are not overwritten.")}catch(e){setError(e instanceof Error?e.message:"Unable to save global defaults")}finally{setBusy(false)}};
 const positions=status?.positions||[];
 return <section className="panel strategy-center">
  <div className="panel-title"><div><h2>Strategy Control Center</h2><p>Manage strategies, shared defaults and a unified view of strategy orders.</p></div><span className="live-label">PAPER FIRST</span></div>
  <nav className="tabs strategy-center-tabs">
   <button className={tab==="strategies"?"tab active":"tab"} onClick={()=>setTab("strategies")}>Strategies</button>
   <button className={tab==="orders"?"tab active":"tab"} onClick={()=>setTab("orders")}>All orders &amp; positions</button>
   <button className={tab==="defaults"?"tab active":"tab"} onClick={()=>setTab("defaults")}>Global defaults</button>
  </nav>
  {error&&<div className="alert">{error}</div>}{notice&&<div className="strategy-notice"><CheckCircle2 size={15}/>{notice}</div>}
  {tab==="strategies"&&<>
   <div className="strategy-center-intro"><div><span className="overview-card-label">STRATEGY REGISTRY</span><h3>Your trading strategies</h3><p>Each strategy keeps its own parameters. Enabling a strategy does not bypass its individual risk checks.</p></div><div className="strategy-count"><strong>3</strong><small>Registered strategies</small></div></div>
   <article className="strategy-registry-card">
    <div className="strategy-registry-icon"><ActivityIcon/></div>
    <div className="strategy-registry-main"><div className="strategy-registry-title"><h3>Opening Price Breakout</h3><span className={settings?.config.enabled?"strategy-state enabled":"strategy-state disabled"}>{settings?.config.enabled?"ENABLED":"DISABLED"}</span><span className={status?.running?"strategy-state running":"strategy-state stopped"}>{status?.running?"RUNNING":"STOPPED"}</span></div><p>Opening range reversal · F&amp;O stocks · ATM CE/PE · {settings?.mode||"PAPER"} mode</p><div className="strategy-mini-stats"><span>Gainers <b>{settings?.config.gainers_count??"—"}</b></span><span>Losers <b>{settings?.config.losers_count??"—"}</b></span><span>Max positions <b>{settings?.config.max_positions??"—"}</b></span><span>Stop loss <b>{settings?.config.stop_loss_points??"—"} pts</b></span></div></div>
    <div className="strategy-registry-actions"><button className={settings?.config.enabled?"secondary-action":"primary"} disabled={busy||!!status?.running||!settings} onClick={()=>void toggleStrategy()}>{settings?.config.enabled?<><PauseCircle size={14}/> Disable</>:<><PlayCircle size={14}/> Enable</>}</button><button className="subtle-button" onClick={onOpenBreakout}><Settings2 size={14}/> Configure <ArrowRight size={13}/></button></div>
   </article>
   <article className="strategy-registry-card price-action-registry-card">
    <div className="strategy-registry-icon"><span className="strategy-activity-mark">⌁</span></div>
    <div className="strategy-registry-main"><div className="strategy-registry-title"><h3>Price Action Intraday</h3><span className="strategy-state enabled">ENABLED</span><span className="strategy-state stopped">PAPER READY</span></div><p>Market Structure · Supply / Demand · Candlestick Context · Liquidity Sweeps · F&amp;O ATM CE/PE</p><div className="strategy-mini-stats"><span>Max positions <b>3</b></span><span>Daily trades <b>5</b></span><span>Risk / Reward <b>1:2</b></span><span>Volume filter <b>Optional</b></span></div></div>
    <div className="strategy-registry-actions"><button className="subtle-button" onClick={onOpenPriceAction}><Settings2 size={14}/> Open Dashboard <ArrowRight size={13}/></button></div>
   </article>
   <article className="strategy-registry-card price-action-registry-card"><div className="strategy-registry-icon"><span className="strategy-activity-mark">☀</span></div><div className="strategy-registry-main"><div className="strategy-registry-title"><h3>Breakfast Strategy</h3><span className="strategy-state enabled">REGISTERED</span><span className="strategy-state stopped">PAPER ONLY</span></div><p>Sector strength / weakness · Top 1–3 stocks · 5-minute opening candle · ATM CE/PE · Equity-linked exits</p><div className="strategy-mini-stats"><span>Entry <b>09:21</b></span><span>Target <b>1%</b></span><span>Stop loss <b>1%</b></span><span>Max positions <b>3</b></span></div></div><div className="strategy-registry-actions"><button className="subtle-button" onClick={onOpenBreakfast}><Settings2 size={14}/> Open Dashboard <ArrowRight size={13}/></button></div></article>
   <div className="strategy-safety-note"><ShieldCheck size={17}/><div><b>Live execution remains safety-gated</b><p>Strategy mode preferences do not enable broker order placement. Paper is the active supported execution path until order placement, protective SL, partial fills and broker reconciliation are fully verified.</p></div></div>
   <div className="future-strategy-panel"><div><h3>Future strategies</h3><p>New strategies will appear here when their actual strategy modules are implemented and registered.</p></div><span>Future strategies can be registered here</span></div>
  </>}
  {tab==="orders"&&<>
   <div className="orders-overview-stats"><div><small>OPEN POSITIONS</small><strong>{status?.open_positions??0}</strong></div><div><small>TRADES TODAY</small><strong>{status?.trades_count??0}</strong></div><div><small>ACTIVE STRATEGIES</small><strong>{status?.running?1:0}</strong></div><div><small>EXECUTION MODE</small><strong>{settings?.mode||"PAPER"}</strong></div></div>
   <div className="section-head"><div><h3>All strategy positions</h3><p>Live positions from currently registered strategy modules</p></div></div>
   {positions.length?<div className="table-scroll"><table className="trade-table"><thead><tr><th>STRATEGY</th><th>STOCK / CONTRACT</th><th>SIDE</th><th>ENTRY TIME</th><th>ENTRY</th><th>LTP</th><th>QTY</th><th>P&amp;L</th><th>STATUS</th></tr></thead><tbody>{positions.map((p:any)=><tr key={p.id}><td>Opening Breakout</td><td><b>{p.symbol}</b><small>{p.option_symbol||"Option quote pending"}</small></td><td>{p.direction||"—"}</td><td>{time(p.opened_at)}</td><td>₹{money(p.entry_price)}</td><td>₹{money(p.ltp)}</td><td>{p.quantity??"—"}</td><td>{typeof p.pnl==="number"?"₹"+money(p.pnl):"—"}</td><td>{p.status}</td></tr>)}</tbody></table></div>:<div className="empty">No open or pending strategy positions.</div>}
   <div className="section-head completed-trades-heading"><div><h3>Completed orders / trades</h3><p>Historical records from registered strategies</p></div><label className="trade-date-filter">Trade date<input type="date" value={tradeDate} max={today()} onChange={e=>setTradeDate(e.target.value)}/></label></div>
   {trades.length?<div className="table-scroll"><table className="trade-table"><thead><tr><th>STRATEGY</th><th>TRADE ID</th><th>STOCK</th><th>MODE</th><th>ENTRY</th><th>EXIT</th><th>QTY</th><th>P&amp;L</th><th>EXIT REASON</th></tr></thead><tbody>{trades.map(t=><tr key={t.id}><td>Opening Breakout</td><td className="trade-id-cell">{t.id}</td><td>{t.symbol}</td><td>{t.mode}</td><td>{time(t.opened_at)}</td><td>{time(t.closed_at)}</td><td>{t.quantity??"—"}</td><td>{typeof t.pnl==="number"?"₹"+money(t.pnl):"—"}</td><td>{t.exit_reason||"—"}</td></tr>)}</tbody></table></div>:<div className="empty">No completed trades for this date.</div>}
  </>}
  {tab==="defaults"&&defaults&&<>
   <div className="strategy-center-intro"><div><span className="overview-card-label">APPLICATION CONFIGURATION</span><h3>Global default settings</h3><p>Baseline values for new strategy modules. Existing strategies retain their own saved parameters.</p></div><span className="defaults-badge">PAPER FIRST</span></div>
   <div className="global-defaults-grid">
    <label>Default execution mode<select value={defaults.default_mode} onChange={e=>setDefaults({...defaults,default_mode:e.target.value as "PAPER"|"LIVE"})}><option value="PAPER">PAPER</option><option value="LIVE">LIVE (preference only)</option></select><small>New strategies start with Paper preference. Live order execution remains locked until safety checks are complete.</small></label>
    <label>Auto-start new strategies<select value={String(defaults.auto_start_new_strategies)} onChange={e=>setDefaults({...defaults,auto_start_new_strategies:e.target.value==="true"})}><option value="false">OFF — manual enable/start</option><option value="true">ON — where strategy supports it</option></select><small>Only applies to future strategy modules that explicitly support auto-start.</small></label>
    <label>Default max open positions<input type="number" min="1" step="1" value={defaults.default_max_positions} onChange={e=>setDefaults({...defaults,default_max_positions:Number(e.target.value)})}/><small>Suggested baseline inherited by new strategies; not a global live order cap yet.</small></label>
    <label>Default stop loss (premium points)<input type="number" min="0.05" step="0.05" value={defaults.default_stop_loss_points} onChange={e=>setDefaults({...defaults,default_stop_loss_points:Number(e.target.value)})}/><small>Baseline only. Each strategy may use a different SL model.</small></label>
    <label>Default exit time (IST)<input type="time" step="1" value={defaults.default_exit_time} onChange={e=>setDefaults({...defaults,default_exit_time:e.target.value})}/><small>Baseline for new intraday strategies unless they define their own exit schedule.</small></label>
   </div>
   <div className="strategy-defaults-warning"><ShieldCheck size={16}/><span>Saving global defaults will not silently overwrite Opening Breakout settings. To change an existing strategy, open its own settings.</span></div>
   <button className="primary" disabled={busy} onClick={()=>void saveDefaults()}>{busy?"Saving…":"Save global defaults"}</button>
  </>}
 </section>;
}

function ActivityIcon(){return <span className="strategy-activity-mark">↗</span>}
