/* ═══ POLYFILLS ═══ */
if(!CanvasRenderingContext2D.prototype.roundRect){CanvasRenderingContext2D.prototype.roundRect=function(x,y,w,h,r){var tl=(r&&r[0])||0;this.moveTo(x+tl,y);this.lineTo(x+w-tl,y);this.arcTo(x+w,y,x+w,y+tl,tl);this.lineTo(x+w,y+h);this.arcTo(x+w,y+h,x+w-tl,y+h,tl);this.lineTo(x+tl,y+h);this.arcTo(x,y+h,x,y+h-tl,tl);this.lineTo(x,y+tl);this.arcTo(x,y,x+tl,y,tl);this.closePath();return this}}

/* ═══ STATE ═══ */
var curveOk=false,tradesOk=false,pricingDone=false,curveData=null,extFileDeals=null,dealMode='manual',lastResults=null,currentStep=1,currentPage='pricer',_chartData={};
var STEP_TITLES=['Données de marché','Instruments','Résultats'];

/* ═══ PORTAL NAVIGATION ═══ */
function goPage(id){
  currentPage=id;
  document.getElementById('page-pricer').style.display=id==='pricer'?'block':'none';
  document.getElementById('page-history').style.display=id==='history'?'block':'none';
  document.getElementById('stepperArea').style.display=id==='pricer'?'block':'none';
  document.querySelectorAll('.snav').forEach(function(n){n.classList.remove('active')});
  var nav=document.getElementById('nav-'+id);if(nav)nav.classList.add('active');
  if(id==='pricer'){document.getElementById('pageTitle').textContent='Pricer Épargne à terme';document.getElementById('stepTitle').style.display=''}
  else if(id==='history'){document.getElementById('pageTitle').textContent='Historique des opérations';document.getElementById('stepTitle').style.display='none';renderHistory()}
}

/* ═══ WIZARD NAVIGATION ═══ */
function goStep(n){
  currentStep=n;
  [1,2,3].forEach(function(i){
    document.getElementById('step'+i).style.display=i===n?'block':'none';
    var ws=document.getElementById('ws'+i);
    ws.className='ws'+(i===n?' active':i<n&&((i===1&&curveOk)||(i===2&&pricingDone))?' done':'');
    if(i<n&&((i===1&&curveOk)||(i===2&&pricingDone)))document.getElementById('wn'+i).innerHTML='<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3"><path d="M5 12l5 5L20 7"/></svg>';
    else document.getElementById('wn'+i).textContent=i;
  });
  [1,2].forEach(function(i){document.getElementById('wl'+i).className='ws-line'+(i<n&&((i===1&&curveOk)||(i===2&&pricingDone))?' done':'')});
  document.getElementById('stepTitle').textContent=STEP_TITLES[n-1];
  if(n===2)updateDealPreview();
  if(n===3&&!pricingDone){document.getElementById('resPanel-summary').innerHTML='<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:240px;gap:16px;background:#fff;border:1px solid rgba(0,0,0,.06);border-radius:14px;padding:40px;text-align:center"><div style="font-size:40px;opacity:.3">📊</div><div style="font-size:16px;font-weight:700;color:#2C2C2E">Aucun résultat</div><div style="font-size:13px;color:#8E8E93">Lancez le pricing à l\'étape 2.</div><button class="btn-sec" onclick="goStep(2)">← Retour</button></div>'}
}
function updateWizard(){if(curveOk){document.getElementById('curveBdg').className='bdg bdg-g';document.getElementById('curveBdg').textContent='✓ Chargée'}}

/* ═══ HELPERS ═══ */
function showSt(id,msg,ok){var el=document.getElementById(id);el.textContent=msg;el.className='status show '+(ok?'ok':'err')}
function fmt(n,dec){if(n==null||isNaN(n))return'—';return parseFloat(n).toLocaleString('fr-CA',{minimumFractionDigits:dec==null?2:dec,maximumFractionDigits:dec==null?2:dec})}
function sf(v,d){if(v==null||isNaN(v))return'—';return parseFloat(v).toFixed(d==null?2:d)}
function fmtM(n){if(Math.abs(n)>=1e9)return fmt(n/1e9,2)+' Md';if(Math.abs(n)>=1e6)return fmt(n/1e6,2)+' M';return fmt(n,0)}
function mkKpi(l,v,u,s,c){return'<div class="kpi"><div class="kl">'+l+'</div><div style="display:flex;align-items:baseline;gap:4px"><span class="kv" style="color:'+(c||'var(--green)')+'">'+v+'</span>'+(u?'<span class="ku">'+u+'</span>':'')+'</div>'+(s?'<div class="ks">'+s+'</div>':'')+'</div>'}
function dc(label,value,cls){return'<div class="dc"><div class="dl">'+label+'</div><div class="dv '+(cls||'')+'">'+value+'</div></div>'}
function tbl(hds,rows){var h='<table class="results-table"><thead><tr>';hds.forEach(function(c){var a=typeof c==='object'?c:{t:c};h+='<th style="text-align:'+(a.r?'right':'left')+'">'+a.t+'</th>'});h+='</tr></thead><tbody>';rows.forEach(function(r){h+='<tr>';r.forEach(function(v,i){var a=typeof hds[i]==='object'?hds[i]:{};h+='<td style="text-align:'+(a.r?'right':'left')+';'+(a.mono?'font-family:var(--mono);':'')+(a.bold?'font-weight:700;':'')+(a.color?'color:'+a.color+';':'')+'">'+v+'</td>'});h+='</tr>'});h+='</tbody></table>';return h}
!function(){var el=document.getElementById('clock');if(!el)return;function t(){el.textContent=new Date().toLocaleTimeString('fr-CA',{hour:'2-digit',minute:'2-digit'})}t();setInterval(t,30000)}();
document.getElementById('footEval').textContent='Évaluation: '+document.getElementById('evalDate').value;

/* ═══ TAB HELPERS ═══ */
function setCrvTab(id,btn){btn.parentElement.querySelectorAll('.wtab').forEach(function(t){t.classList.remove('active')});btn.classList.add('active');document.getElementById('crvSql').style.display=id==='sql'?'block':'none';document.getElementById('crvCsv').style.display=id==='csv'?'block':'none'}
function setVolTab(id,btn){btn.parentElement.querySelectorAll('.wtab').forEach(function(t){t.classList.remove('active')});btn.classList.add('active');document.getElementById('volProxy').style.display=id==='proxy'?'block':'none';document.getElementById('volFile').style.display=id==='file'?'block':'none'}
function setDealTab(id,btn){document.querySelectorAll('#dealTabs .wtab').forEach(function(t){t.classList.remove('active')});btn.classList.add('active');['manual','upload','portfolio'].forEach(function(t){document.getElementById('deal'+t.charAt(0).toUpperCase()+t.slice(1)).style.display=t===id?'block':'none'});dealMode=id}
function showRes(id,btn){document.querySelectorAll('.rtab').forEach(function(t){t.classList.remove('active')});if(btn)btn.classList.add('active');else{document.querySelectorAll('.rtab').forEach(function(t){if(t.getAttribute('onclick').indexOf("'"+id+"'")>=0)t.classList.add('active')})}document.querySelectorAll('[id^="resPanel-"]').forEach(function(p){p.style.display='none'});var el=document.getElementById('resPanel-'+id);if(el){el.style.display='block';setTimeout(function(){_drawDeferredCharts(id)},80)}}

/* ═══ STEP 1: COURBE ═══ */
function fetchCurveCDF(){var btn=document.getElementById('btnFetch'),ev=document.getElementById('evalDate').value;btn.disabled=true;btn.textContent='⟳ Chargement...';fetch('/cpg/api/fetch_curve_cdf',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({eval_date:ev})}).then(function(r){return r.json()}).then(function(d){btn.disabled=false;btn.textContent='⚡ Charger depuis QRM Staging';if(d.error){showSt('curveStatus','⚠ '+d.error,false);curveOk=false}else{showSt('curveStatus','✓ '+d.points+' pts ('+d.range+')',true);curveOk=true;curveData=d.preview;document.getElementById('curvePts').textContent=d.points+' points';if(d.preview){showCurveTable(d.preview);drawCurveChart(d.preview)}}updateWizard()}).catch(function(e){btn.disabled=false;btn.textContent='⚡ Charger depuis QRM Staging';showSt('curveStatus','⚠ '+e,false)})}
function loadCurveFile(i){var f=i.files[0];if(!f)return;var fd=new FormData();fd.append('file',f);fetch('/cpg/api/upload_curve',{method:'POST',body:fd}).then(function(r){return r.json()}).then(function(d){if(d.error){showSt('curveStatus','⚠ '+d.error,false);curveOk=false}else{showSt('curveStatus','✓ '+d.points+' pts',true);curveOk=true;curveData=d.preview;document.getElementById('curvePts').textContent=d.points+' points';if(d.preview){showCurveTable(d.preview);drawCurveChart(d.preview)}}updateWizard()})}
function showCurveTable(rows){document.getElementById('curveTableP').style.display='block';var h='<table class="results-table"><thead><tr><th>Terme</th><th style="text-align:right">Spread</th><th style="text-align:right">OIS</th><th style="text-align:right">Taux CDF</th><th style="text-align:right">Jours</th></tr></thead><tbody>';rows.forEach(function(r){h+='<tr><td>'+(r.termPoint||'')+' '+(r.termType||'')+'</td><td style="text-align:right">'+sf(r.ZeroCouponSpreadCDF||0,4)+'</td><td style="text-align:right">'+sf(r.ZeroCouponBase||0,4)+'</td><td style="text-align:right;font-weight:700;color:var(--green)">'+sf(r.TauxCDF||0,4)+'</td><td style="text-align:right;color:#8E8E93">'+(r.ApproxDays||0)+'</td></tr>'});h+='</tbody></table>';document.getElementById('curvePreview').innerHTML=h}
function drawCurveChart(rows){document.getElementById('curveChartP').style.display='block';var c=document.getElementById('curveCanvas'),ctx=c.getContext('2d'),W=c.parentElement.offsetWidth-16,H=180;if(W<50)return;c.width=W*2;c.height=H*2;c.style.width=W+'px';c.style.height=H+'px';ctx.scale(2,2);var pts=rows.map(function(r){return{x:r.ApproxDays||0,y:parseFloat(r.TauxCDF||0)}}).filter(function(p){return!isNaN(p.y)});if(!pts.length)return;var xMax=Math.max.apply(null,pts.map(function(p){return p.x}))*1.05,yMin=Math.min.apply(null,pts.map(function(p){return p.y}))*.95,yMax=Math.max.apply(null,pts.map(function(p){return p.y}))*1.05,pad={t:16,r:16,b:24,l:50},pw=W-pad.l-pad.r,ph=H-pad.t-pad.b;function tx(v){return pad.l+v/xMax*pw}function ty(v){return pad.t+(1-(v-yMin)/(yMax-yMin))*ph}ctx.fillStyle='#fff';ctx.fillRect(0,0,W,H);ctx.strokeStyle='#E5E5EA';ctx.lineWidth=.5;for(var i=0;i<4;i++){var y=yMin+(yMax-yMin)*i/3;ctx.beginPath();ctx.moveTo(pad.l,ty(y));ctx.lineTo(W-pad.r,ty(y));ctx.stroke();ctx.fillStyle='#8E8E93';ctx.font='9px system-ui';ctx.textAlign='right';ctx.fillText(y.toFixed(2)+'%',pad.l-4,ty(y)+3)}var grad=ctx.createLinearGradient(0,pad.t,0,H-pad.b);grad.addColorStop(0,'rgba(0,135,78,.1)');grad.addColorStop(1,'rgba(0,135,78,.01)');ctx.beginPath();ctx.moveTo(tx(pts[0].x),ty(pts[0].y));pts.forEach(function(p){ctx.lineTo(tx(p.x),ty(p.y))});ctx.lineTo(tx(pts[pts.length-1].x),H-pad.b);ctx.lineTo(tx(pts[0].x),H-pad.b);ctx.closePath();ctx.fillStyle=grad;ctx.fill();ctx.beginPath();ctx.moveTo(tx(pts[0].x),ty(pts[0].y));pts.forEach(function(p){ctx.lineTo(tx(p.x),ty(p.y))});ctx.strokeStyle='#00874E';ctx.lineWidth=2;ctx.stroke();pts.forEach(function(p){ctx.beginPath();ctx.arc(tx(p.x),ty(p.y),3,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.strokeStyle='#00874E';ctx.lineWidth=1.5;ctx.stroke()})}

/* ═══ STEP 1: VOL ═══ */
function applyVolProxy(){fetch('/cpg/api/vol/proxy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({vol_base:+document.getElementById('volBase').value,slope:+document.getElementById('volSlope').value,floor:+document.getElementById('volFloor').value})}).then(function(r){return r.json()}).then(function(d){if(d.error){showSt('volStatus','⚠ '+d.error,false);return}showSt('volStatus','✓ Surface proxy générée ('+d.points+' pts)',true);showVolPreview(d)})}
function showVolPreview(d){var el=document.getElementById('volPreview');if(!el||!d.vol_matrix||!d.tenor_grid)return;el.style.display='block';var h='<table class="results-table" style="font-size:10px"><thead><tr><th style="background:#fafafa">Exp \\ Ténor</th>';d.tenor_grid.forEach(function(t){h+='<th style="text-align:right;background:#fafafa">'+sf(t,0)+'A</th>'});h+='</tr></thead><tbody>';var flat=[].concat.apply([],d.vol_matrix),vMn=Math.min.apply(null,flat),vMx=Math.max.apply(null,flat);d.vol_matrix.forEach(function(row,i){h+='<tr><td style="font-weight:600;background:#fafafa">'+sf(d.expiry_grid[i],1)+'A</td>';row.forEach(function(v){var p=(v-vMn)/(vMx-vMn||1);h+='<td style="text-align:right;font-family:var(--mono);font-weight:600;background:rgba('+(240-p*80)+','+(240+p*15)+','+(240-p*100)+',.15)">'+sf(v,1)+'</td>'});h+='</tr>'});h+='</tbody></table>';el.innerHTML=h}
function loadVolFile(i){var f=i.files[0];if(!f)return;var fd=new FormData();fd.append('file',f);fetch('/cpg/api/vol/upload',{method:'POST',body:fd}).then(function(r){return r.json()}).then(function(d){if(d.error)showSt('volStatus','⚠ '+d.error,false);else showSt('volStatus','✓ '+d.points+' pts ('+d.source+')',true)})}

/* ═══ STEP 2: INSTRUMENTS ═══ */
function extTypeChanged(){var t=document.getElementById('extType').value;if(t==='LINEAR ACCRUAL'){document.getElementById('extRate').value='6.05';document.getElementById('extFreq').value='0';document.getElementById('extFinalMat').value='2040-10-02'}else{document.getElementById('extRate').value='4.10';document.getElementById('extFreq').value='1';document.getElementById('extFinalMat').value='2035-10-02'}updateDealPreview()}
function updateDealPreview(){var t=document.getElementById('extType').value,r=document.getElementById('extRate').value,em=document.getElementById('extEmission').value,im=document.getElementById('extInitMat').value,fm=document.getElementById('extFinalMat').value;var y1=((new Date(im)-new Date(em))/365.25/86400000).toFixed(0),y2=((new Date(fm)-new Date(em))/365.25/86400000).toFixed(0);document.getElementById('dealPreview').innerHTML='<strong>'+t+'</strong> à '+r+'% · '+y1+'–'+y2+' ans · '+(y2-y1)+' dates d\'exercice'}
function loadExtFile(inp){var f=inp.files[0];if(!f)return;var fd=new FormData();fd.append('file',f);fetch('/cpg/api/upload_ext_trades',{method:'POST',body:fd}).then(function(r){return r.json()}).then(function(d){
  console.log('[UPLOAD] Response:',JSON.stringify(d).slice(0,500));
  if(d.error){showSt('extFileStatus','⚠ '+d.error,false);extFileDeals=null;return}
  extFileDeals=d.deals;
  console.log('[UPLOAD] extFileDeals set:',extFileDeals.length,'deals, first=',JSON.stringify(extFileDeals[0]));
  showSt('extFileStatus','✓ '+d.count+' instruments chargés',true);
  document.getElementById('btnPopulate').style.display='inline-block';var h='<table class="results-table"><thead><tr><th>Type</th><th>FundServ</th><th style="text-align:right">Montant</th><th style="text-align:right">Taux</th><th>Init.</th><th>Finale</th></tr></thead><tbody>';d.deals.forEach(function(dl){h+='<tr><td><span class="bdg '+(dl.cpg_type==='COUPON'?'bdg-g':'bdg-b')+'">'+dl.cpg_type+'</span></td><td style="font-family:var(--mono);font-size:10px">'+dl.fundserv+'</td><td style="text-align:right;font-family:var(--mono)">'+fmtM(dl.notional)+'</td><td style="text-align:right;font-family:var(--mono)">'+sf(dl.client_rate,2)+'%</td><td style="font-size:10px">'+dl.initial_maturity+'</td><td style="font-size:10px">'+dl.final_maturity+'</td></tr>'});h+='</tbody></table>';document.getElementById('extFilePreview').innerHTML=h
}).catch(function(e){console.error('[UPLOAD] Failed:',e);showSt('extFileStatus','⚠ '+e.message,false);extFileDeals=null})}
function populateFormFromFile(){if(!extFileDeals||!extFileDeals.length)return;var d=extFileDeals[0];document.getElementById('extType').value=d.cpg_type||'COUPON';document.getElementById('extFund').value=d.fundserv||'';document.getElementById('extNot').value=d.notional||10000;document.getElementById('extRate').value=d.client_rate||4.10;document.getElementById('extFreq').value=d.freq_per_year||1;document.getElementById('extEmission').value=d.emission||'';document.getElementById('extInitMat').value=d.initial_maturity||'';document.getElementById('extFinalMat').value=d.final_maturity||'';setDealTab('manual',document.querySelector('#dealTabs .wtab'));updateDealPreview()}
function loadTradesFile(inp){var f=inp.files[0];if(!f)return;var fd=new FormData();fd.append('file',f);fetch('/cpg/api/upload_trades',{method:'POST',body:fd}).then(function(r){return r.json()}).then(function(d){if(d.error){showSt('tradesStatus','⚠ '+d.error,false);tradesOk=false}else{showSt('tradesStatus','✓ '+d.count+' trades chargés',true);tradesOk=true}})}

/* ═══ PRICING ═══ */
function runAllPricing(){
  var btn=document.getElementById('btnPrice');btn.disabled=true;btn.textContent='⟳ Pricing...';
  var ev=document.getElementById('evalDate').value;

  function setStatus(msg,isErr){
    var el=document.getElementById('resPanel-summary');
    if(el)el.innerHTML='<div style="padding:40px;text-align:center;'+(isErr?'color:#FF3B30':'color:#8E8E93')+'"><div style="font-size:14px;font-weight:600;margin-bottom:8px">'+msg+'</div>'+(isErr?'':'<div style="width:24px;height:24px;border:2px solid #E5E5EA;border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite;display:inline-block;margin-top:8px"></div>')+'</div>';
  }
  function resetBtn(){btn.disabled=false;btn.textContent='▶ Lancer le pricing'}

  if(dealMode==='portfolio'){
    fetch('/cpg/api/price',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({eval_date:ev})})
    .then(function(r){return r.json()})
    .then(function(d){resetBtn();if(d.error){alert(d.error);return}pricingDone=true;saveRun({type:'PORTEFEUILLE',notional:d.notional_total},{pv_total:d.pv_total,n:d.count_ok});goStep(3);renderPortfolioResults(d);fetchAndAppendGreeks()})
    .catch(function(e){resetBtn();alert('Erreur: '+e)});
    return;
  }

  /* Build deals array */
  var deals=[];
  if(dealMode==='upload'&&extFileDeals&&extFileDeals.length>0){
    deals=extFileDeals;
    console.log('[PRICING] Upload mode, '+deals.length+' deals');
  } else {
    deals=[{cpg_type:document.getElementById('extType').value,fundserv:document.getElementById('extFund').value,notional:+document.getElementById('extNot').value,client_rate:+document.getElementById('extRate').value,emission:document.getElementById('extEmission').value,initial_maturity:document.getElementById('extInitMat').value,final_maturity:document.getElementById('extFinalMat').value,freq_per_year:+document.getElementById('extFreq').value}];
    console.log('[PRICING] Manual mode, 1 deal');
  }

  /* Validate */
  var d0=deals[0];
  if(!d0||!d0.emission||!d0.initial_maturity||!d0.final_maturity){
    resetBtn();goStep(3);setStatus('Dates manquantes. Vérifiez émission, échéance initiale et finale.',true);return;
  }
  if(!curveOk){
    resetBtn();goStep(3);setStatus('Courbe non chargée. Chargez la courbe à l\'étape 1 avant de pricer.',true);return;
  }

  /* Show step 3 with spinner */
  goStep(3);
  setStatus('⟳ Pricing de '+deals.length+' instrument'+(deals.length>1?'s':'')+'...');

  /* Price all deals */
  var results=[];
  var errors=[];

  function priceDeal(idx){
    if(idx>=deals.length){
      /* All done — render */
      resetBtn();
      console.log('[PRICING] Complete: '+results.length+' OK, '+errors.length+' errors');
      if(!results.length){
        setStatus('Aucun instrument pricé avec succès.'+(errors.length?' Erreurs: '+errors.map(function(e){return e.msg}).join('; '):''),true);
        return;
      }
      pricingDone=true;
      try{
        if(results.length===1&&results[0].deal_summary){renderFullResults(results[0])}
        else{renderExtPortfolio(results)}
        showRes('summary');
      }catch(err){
        console.error('[PRICING] Render error:',err);
        document.getElementById('resPanel-summary').innerHTML='<div style="padding:20px;color:#FF3B30"><strong>Erreur de rendu :</strong> '+err.message+'<pre style="margin-top:8px;font-size:10px;background:#f5f5f7;padding:10px;border-radius:8px;overflow-x:auto;white-space:pre-wrap">'+err.stack+'</pre></div>';
      }
      return;
    }

    setStatus('⟳ Instrument '+(idx+1)+' / '+deals.length+'...');
    var deal=deals[idx];
    var payload=Object.assign({},deal,{eval_date:ev});
    var endpoint=(deals.length===1)?'/cpg/api/full_results':'/cpg/api/price_extendible';

    fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(function(r){
      if(!r.ok){return r.text().then(function(t){throw new Error('HTTP '+r.status+': '+t.slice(0,100))})}
      return r.json();
    })
    .then(function(d){
      if(d.error){
        console.warn('[PRICING] Deal '+idx+' error:',d.error.split('\n')[0]);
        errors.push({idx:idx,msg:d.error.split('\n')[0]});
      } else {
        console.log('[PRICING] Deal '+idx+' OK: PV='+d.PV_total);
        results.push(d);
        saveRun(deal,d);
      }
      priceDeal(idx+1);
    })
    .catch(function(e){
      console.error('[PRICING] Deal '+idx+' fatal:',e);
      errors.push({idx:idx,msg:e.message||String(e)});
      priceDeal(idx+1);
    });
  }

  priceDeal(0);
}


/* ═══ RENDER: RÉSULTATS COMPLETS ═══ */
function renderFullResults(b){
  var v=b.valuation,ds=b.deal_summary,pr=b.premium,g=b.greeks||{},sc=b.schedule||{},cf=b.cashflows||{},cu=b.curve_summary||{},ca=b.calibration||{},kr=b.key_rate_risk||{},vm=b.vol_monitor||{};
  /* SYNTHÈSE */
  var s='<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:18px">';
  s+=mkKpi('VAN',fmt(v.npv),'$','Total','var(--green)');s+=mkKpi('VA Fixe',fmt(v.pv_fixed),'$','Flux certains','#007AFF');
  s+=mkKpi('Option',fmt(v.option_value),'$',v.option_method||'','#FF9500');s+=mkKpi('Taux client',sf(v.atm_strike,2)+' %','','','#2C2C2E');
  s+=mkKpi('Option/VAN',sf(v.premium_pct,2)+' %','','','#5856D6');s+='</div>';
  s+='<div class="block"><div class="panel"><div class="panel-header"><div class="dot"></div>Fiche de l\'instrument</div><div class="panel-body"><div class="dgrid" style="grid-template-columns:repeat(4,1fr)">';
  [['Style',ds.style],['Position',ds.position],['Type',ds.type],['Devise',ds.currency],['Nominal',fmtM(ds.notional)+' $'],['Taux client',sf(ds.strike,2)+' %'],['1er exercice',ds.first_exercise],['Règlement',ds.settlement],['Début',ds.swap_start],['Fin',ds.swap_end],['Exercices',ds.n_exercise_dates+' dates'],['Durée',ds.min_years+'A–'+ds.max_years+'A'],['Modèle',ds.model],['Vol',ds.vol_type],['Date courbe',ds.curve_date],['FundServ',ds.fundserv||'—']].forEach(function(p){s+=dc(p[0],p[1])});
  s+='</div></div></div></div>';
  s+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px" class="block"><div class="panel"><div class="panel-header"><div class="dot" style="background:var(--green)"></div>Évaluation</div><div class="panel-body"><div class="dgrid" style="grid-template-columns:1fr 1fr">';
  s+=dc('VAN',fmt(v.npv)+' $','green');s+=dc('VAN sans frais',fmt(v.npv_without_fee)+' $','green');s+=dc('Taux client',sf(v.atm_strike,4)+' %');s+=dc('VAN/Nominal',sf(v.pv_notional_ratio,2)+' %');s+=dc('Valeur intrinsèque',fmt(v.intrinsic_value)+' $','orange');s+=dc('Valeur temps',fmt(v.time_value)+' $'+(v.time_value>0?' (HW1F)':''),'purple');
  s+='</div></div></div><div class="panel"><div class="panel-header"><div class="dot" style="background:#5856D6"></div>Primes</div><div class="panel-body"><div class="dgrid" style="grid-template-columns:1fr 1fr">';
  s+=dc('Prime option',fmt(pr.option_premium)+' $','orange');s+=dc('Prime option %',sf(pr.option_premium_pct,4)+' %');s+=dc('Sous-jacent',fmt(pr.underlying_premium)+' $','blue');s+=dc('Sous-jacent %',sf(pr.underlying_premium_pct,4)+' %');s+=dc('Total',fmt(pr.total_premium)+' $','green');s+=dc('Option/Total',sf(pr.option_over_total_pct,2)+' %');
  s+='</div></div></div></div>';
  /* HW1F Vega (if available) */
  if(v.hw_vega_1bp){s+='<div class="block" style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">';s+=mkKpi('Véga HW1F',fmt(v.hw_vega_1bp,4),'$/pb',v.hw_vega_source||'Bump σ dans l\'arbre','#3A3A3C');s+=mkKpi('σ modèle',sf(v.hw_sigma_bp||ds.hw_sigma_bp,0)+' pb','','Vol normale','#636366');s+=mkKpi('Rétro. moyenne',sf(v.hw_mean_reversion||ds.hw_mean_reversion,3),'','a (HW1F)','#636366');s+='</div>'}
  if(b.remboursement_schedule&&b.remboursement_schedule.length){var rs=b.remboursement_schedule;s+='<div class="block"><div class="panel"><div class="panel-header"><div class="dot" style="background:#5856D6"></div>Barème de remboursement</div><div class="panel-body" style="padding:0;overflow-x:auto">';s+=tbl([{t:'Année'},{t:'Date'},{t:'Cumulatif',r:1,mono:1},{t:'Rend. ann.',r:1,mono:1},{t:'Remb.',r:1,mono:1,bold:1,color:'var(--green)'},{t:'Montant',r:1,mono:1}],rs.map(function(r){return['An '+r.year,r.date,sf(r.cumulative_rate_pct,2)+' %',sf(r.annualized_yield_pct,2)+' %',sf(r.remboursement_pct,2)+' %',fmt(r.remboursement_amount)]}));s+='</div></div></div>'}
  document.getElementById('resPanel-summary').innerHTML=s;
  /* GREEKS */
  var s2='';
  if(g.available){s2+='<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:18px">';s2+=mkKpi('DV01',fmt(g.dv01),'$/pb',g.dv01_method||'','#FF3B30');s2+=mkKpi('CS01',fmt(g.cs01),'$/pb','Spread','#FF9500');s2+=mkKpi('Gamma',fmt(g.gamma_1bp,4),'$/pb²','','#5856D6');s2+=mkKpi('Thêta 1j',fmt(g.theta_1d),'$','Portage: '+sf(g.carry_bps,2)+' pb','#007AFF');s2+=mkKpi('Thêta 1m',fmt(g.theta_1m),'$','','#007AFF');s2+=mkKpi('Véga',fmt(g.vega_1bp),'$',g.vega_source||'','#3A3A3C');s2+='</div>';
  s2+='<div class="panel block"><div class="panel-header"><div class="dot"></div>Détail des sensibilités</div><div class="panel-body"><div class="dgrid" style="grid-template-columns:repeat(4,1fr)">';s2+=dc('VA Base',fmt(g.pv_base)+' $','green');s2+=dc('DV01',fmt(g.dv01)+' $/pb','red');s2+=dc('CS01',fmt(g.cs01)+' $/pb','orange');s2+=dc('Gamma',sf(g.gamma_1bp,4)+' $/pb²','purple');s2+=dc('Véga',fmt(g.vega_1bp)+' $');s2+=dc('Thêta',fmt(g.theta_1d)+' $','blue');s2+=dc('Portage',sf(g.carry_bps,2)+' pb/j');s2+=dc('Méthode courbe',g.curve_method||'—');s2+='</div></div></div>'}
  else{s2+='<div style="padding:40px;text-align:center;color:#8E8E93">Sensibilités non disponibles. '+(g.note||'Charger un portefeuille.')+'</div>'}
  document.getElementById('resPanel-greeks').innerHTML=s2;
  /* EXERCICE */
  var scRows=sc.rows||[];var s3='<div class="panel block"><div class="panel-header"><div class="dot" style="background:#FF9500"></div>Calendrier d\'exercice<span class="bdg bdg-g" style="margin-left:auto">'+(sc.itm_count||0)+'/'+(sc.n_exercises||0)+' ITM</span></div><div class="panel-body" style="padding:0;overflow-x:auto">';
  s3+=tbl([{t:'#'},{t:'Date'},{t:'Résid.',r:1},{t:'Strike',r:1,mono:1,bold:1},{t:'Fwd OIS',r:1,mono:1},{t:'Spread',r:1,mono:1},{t:'Fwd Fin.',r:1,mono:1,bold:1},{t:'Moneyness',r:1,mono:1},{t:'Intr. ($)',r:1,mono:1},{t:''}],scRows.map(function(r){return[r.n||'',r.exercise_date||'',r.residual_days+'j',sf(r.strike_pct,3),sf(r.ois_forward_pct,3),'<span style="color:var(--amber)">'+sf(r.spread_market_pct,3)+'</span>',sf(r.funding_forward_pct,3),'<span class="'+(r.in_the_money?'diff-good':'diff-bad')+'">'+(r.moneyness_bp>=0?'+':'')+sf(r.moneyness_bp,1)+' pb</span>',fmt(r.intrinsic_pv),'<span class="bdg '+(r.in_the_money?'bdg-g':'bdg-r')+'" style="font-size:9px">'+(r.in_the_money?'ITM':'HEM')+'</span>'+(r.optimal?' ★':'')]}));
  s3+='</div></div><div class="panel block"><div class="panel-header"><div class="dot"></div>Profil de moneyness</div><div class="panel-body" style="padding:8px"><canvas id="moneyC" height="160" style="width:100%"></canvas></div></div>';
  document.getElementById('resPanel-schedule').innerHTML=s3;
  var exA=b.exercise_analysis||[];if(exA.length>1){_chartData.moneyness={vals:exA.map(function(e){return e.moneyness_bp}),labels:exA.map(function(e){return e.exercise_date.slice(2,7)})}}
  /* FLUX */
  var cfRows=cf.rows||[];var s4='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px">';s4+=mkKpi('Réceptions',fmt(cf.total_receive),'$','','var(--green)');s4+=mkKpi('Versements',fmt(cf.total_pay),'$','','#FF3B30');s4+=mkKpi('Net',fmt(cf.total_net),'$','','#007AFF');s4+=mkKpi('VA',fmt(cf.pv_total),'$','Actualisée','#5856D6');s4+='</div>';
  s4+='<div class="panel block"><div class="panel-header"><div class="dot"></div>Tableau des flux<span class="bdg bdg-d" style="margin-left:auto">'+(cf.n_cashflows||0)+'</span></div><div class="panel-body" style="padding:0;overflow-x:auto">';
  s4+=tbl([{t:'#'},{t:'Date'},{t:'Type'},{t:'Réception',r:1,mono:1,color:'var(--green)'},{t:'Versement',r:1,mono:1,color:'#FF3B30'},{t:'Net',r:1,mono:1,bold:1}],cfRows.map(function(r){return[r.n||'',r.pay_date||'',r.type||'',fmt(r.receive),fmt(r.pay),fmt(r.net)]}));s4+='</div></div>';
  document.getElementById('resPanel-cashflows').innerHTML=s4;
  /* COURBE */
  var cuPts=cu.points||[];var s5='<div class="panel block"><div class="panel-header"><div class="dot"></div>Résumé de la courbe</div><div class="panel-body"><div class="dgrid" style="grid-template-columns:repeat(3,1fr)">';
  s5+=dc('Courbe',cu.curve_name||'');s5+=dc('Composantes',cu.components||'');s5+=dc('Interpolation',cu.interpolation||'');s5+=dc('Date',cu.curve_date||'');s5+=dc('DV01',cu.dv01_calc_type||'');s5+=dc('Points',(cu.n_points||0)+' ('+(cu.range_days||'')+')');s5+='</div></div></div>';
  var cH=[{t:'Terme'},{t:'Jours',r:1},{t:'Années',r:1},{t:'Taux (%)',r:1,mono:1,bold:1,color:'var(--green)'}];if(cu.decomposition_available){cH.push({t:'OIS',r:1,mono:1},{t:'Spread',r:1,mono:1,color:'var(--amber)'})}cH.push({t:'Facteur d\'act.',r:1,mono:1});
  s5+='<div class="panel block"><div class="panel-header"><div class="dot"></div>Points de courbe</div><div class="panel-body" style="padding:0;overflow-x:auto">';
  s5+=tbl(cH,cuPts.map(function(p){var row=[p.term||'',p.days||0,p.years||0,sf(p.market_rate,4)];if(cu.decomposition_available){row.push(p.zero_rate_ois!=null?sf(p.zero_rate_ois,4):'—',p.zero_rate_spread!=null?sf(p.zero_rate_spread,4):'—')}row.push(sf(p.discount_factor,6));return row}));s5+='</div></div>';
  document.getElementById('resPanel-curve').innerHTML=s5;
  /* MODÈLE */
  var s6='<div class="panel block"><div class="panel-header"><div class="dot"></div>Paramètres du modèle</div><div class="panel-body"><div class="dgrid" style="grid-template-columns:repeat(3,1fr)">';
  s6+=dc('Modèle',ca.model||'');s6+=dc('Méthode',ca.calibration_method||'');s6+=dc('Rétro. moy.',ca.mean_reversion||'');s6+=dc('Type σ',ca.sigma_type||'');s6+=dc('Moteur',ca.pricing_engine||'');s6+=dc('Spread',ca.spread_treatment||'');
  s6+='</div><div style="margin-top:14px;padding:12px;background:#FFFBF5;border-radius:8px;border:1px solid #FDE68A;font-size:12px;color:#92400E"><strong>Statut :</strong> '+(ca.status||'')+'</div>';
  s6+='<div style="margin-top:10px;padding:12px;background:var(--bg3);border-radius:8px;font-size:11px;color:#636366;line-height:1.6">'+(ca.note||'')+'</div></div></div>';
  var spI=b.spread_initial;if(spI&&spI.term_structure){var ts=spI.term_structure,mx=Math.max.apply(null,ts.map(function(s){return s.spread_bp}));s6+='<div class="panel block"><div class="panel-header"><div class="dot" style="background:var(--amber)"></div>Structure de spread par terme</div><div class="panel-body" style="padding:0;overflow-x:auto">';s6+=tbl([{t:'Terme'},{t:'Spread (pb)',r:1,mono:1,bold:1},{t:''}],ts.map(function(p){return[(p.days/365).toFixed(1)+'A ('+p.days+'j)',sf(p.spread_bp,1),'<div class="krbar" style="width:80px"><div class="krbar-f" style="width:'+(mx>0?p.spread_bp/mx*100:0)+'%;background:var(--amber)"></div></div>']}));s6+='</div></div>'}
  document.getElementById('resPanel-calib').innerHTML=s6;
  /* VOLATILITÉ */
  var s7='';
  if(vm.available){s7+='<div class="panel block"><div class="panel-header"><div class="dot"></div>Surface de volatilité<span class="bdg bdg-g" style="margin-left:auto">'+(vm.n_expiries||0)+'×'+(vm.n_tenors||0)+'</span></div><div class="panel-body" style="padding:0;overflow-x:auto"><table class="results-table" style="font-size:10px"><thead><tr><th>Exp\\Ténor</th>';(vm.tenor_grid||[]).forEach(function(t){s7+='<th style="text-align:right">'+sf(t,1)+'A</th>'});s7+='</tr></thead><tbody>';var fl=(vm.matrix||[]).reduce(function(a,b){return a.concat(b)},[]),vMn=Math.min.apply(null,fl),vMx=Math.max.apply(null,fl);(vm.matrix||[]).forEach(function(row,i){s7+='<tr><td>'+(vm.expiry_grid||[])[i]+'A</td>';row.forEach(function(val){var p=(val-vMn)/(vMx-vMn||1);s7+='<td style="text-align:right;background:rgba('+(240-p*80)+','+(240+p*15)+','+(240-p*100)+',.15);font-family:var(--mono);font-weight:600">'+sf(val,1)+'</td>'});s7+='</tr>'});s7+='</tbody></table></div></div>'}
  else{s7+='<div style="padding:40px;text-align:center;color:#8E8E93"><p>'+(vm.source||'Non disponible')+'</p><p style="font-size:12px;margin-top:4px">'+(vm.note||'')+'</p></div>'}
  document.getElementById('resPanel-vol').innerHTML=s7;
  /* RISQUE CLÉ */
  var s8='';
  if(kr.available){s8+='<div class="panel block"><div class="panel-header"><div class="dot" style="background:#FF3B30"></div>Risque par point clé</div><div class="panel-body"><div class="dgrid" style="grid-template-columns:repeat(4,1fr)">';s8+=dc('DV01 global',fmt(kr.dv01_global)+' $/pb','red');s8+=dc('Choc',kr.shift+' pb');s8+=dc('Méthode',kr.bump_method||'');s8+=dc('Courbe',kr.curve_bumped||'');s8+='</div></div></div>';
  s8+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px" class="block"><div class="panel"><div class="panel-header"><div class="dot" style="background:#FF3B30"></div>Tableau DV01 par point</div><div class="panel-body" style="padding:0">';
  var krB=(kr.buckets||[]).filter(function(b){return Math.abs(b.dv01)>0.001});
  s8+=tbl([{t:'Point'},{t:'DV01',r:1,mono:1,bold:1},{t:'%',r:1},{t:''}],krB.map(function(b){return[b.bucket,fmt(b.dv01),sf(b.pct_total,1)+'%','<div class="krbar"><div class="krbar-f" style="width:'+Math.min(b.pct_total||0,100)+'%"></div></div>']}));
  s8+='</div></div><div class="panel"><div class="panel-header"><div class="dot" style="background:#FF3B30"></div>Distribution</div><div class="panel-body" style="padding:8px"><canvas id="krC2" height="200" style="width:100%"></canvas></div></div></div>';
  _chartData.krr={vals:krB.map(function(b){return b.dv01}),labels:krB.map(function(b){return b.bucket})}}
  else{s8+='<div style="padding:40px;text-align:center;color:#8E8E93">Non disponible. '+(kr.note||'')+'</div>'}
  document.getElementById('resPanel-krr').innerHTML=s8;
  /* SCÉNARIOS */
  var scens=b.scenarios||[];var s9='';
  if(scens.length){s9+='<div class="panel block"><div class="panel-header"><div class="dot" style="background:var(--amber)"></div>Scénarios de taux<span class="bdg bdg-d" style="margin-left:auto">'+scens.length+'</span></div><div class="panel-body" style="padding:0;overflow-x:auto">';
  s9+=tbl([{t:'Scénario'},{t:'Type'},{t:'VA',r:1,mono:1,bold:1},{t:'ΔVA',r:1,mono:1},{t:'Δ%',r:1,mono:1}],scens.map(function(s){return[s.scenario,s.type||'',fmt(s.PV),'<span class="'+(s.delta_PV>0?'diff-good':'diff-bad')+'">'+(s.delta_PV>=0?'+':'')+fmt(s.delta_PV)+'</span>',sf(s.delta_pct,3)+'%']}));s9+='</div></div>'}
  else{s9+='<div style="padding:40px;text-align:center;color:#8E8E93">Disponibles après pricing portefeuille.</div>'}
  document.getElementById('resPanel-scenarios').innerHTML=s9;
  document.getElementById('resSubtitle').textContent=ds.type+' '+ds.fundserv+' · VAN : '+fmt(v.npv)+' $';
}

/* ═══ RENDER: PORTEFEUILLE ═══ */
function renderExtPortfolio(results){
  var pvT=0,pvF=0,optT=0,intrT=0,timeT=0;
  results.forEach(function(r){pvT+=r.PV_total||0;pvF+=r.PV_fixed||0;optT+=r.option_value||0;intrT+=r.intrinsic_value||0;timeT+=r.time_value||0});
  var h='<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:18px">';
  h+=mkKpi('VA Totale',fmt(pvT),'$',results.length+' instruments','var(--green)');
  h+=mkKpi('VA Fixe',fmt(pvF),'$','Flux certains','#007AFF');
  h+=mkKpi('Option',fmt(optT),'$','Bermudiennes','#FF9500');
  h+=mkKpi('Intrinsèque',fmt(intrT),'$','','#3A3A3C');
  h+=mkKpi('Time Value',fmt(timeT),'$','HW1F','#5856D6');
  h+='</div>';
  h+='<div class="panel block"><div class="panel-header"><div class="dot"></div>Détail par instrument<span class="bdg bdg-g" style="margin-left:auto">'+results.length+'</span></div><div class="panel-body" style="padding:0;overflow-x:auto">';
  h+=tbl([{t:'Type'},{t:'CUSIP'},{t:'FundServ'},{t:'Taux',r:1,mono:1},{t:'Init.'},{t:'Finale'},{t:'VA',r:1,mono:1,bold:1,color:'var(--green)'},{t:'VA Fixe',r:1,mono:1},{t:'Option',r:1,mono:1,color:'#FF9500'},{t:'Time',r:1,mono:1,color:'#5856D6'},{t:'Méthode'}],
    results.map(function(r){return[
      '<span class="bdg '+(r.cpg_type==='COUPON'?'bdg-g':'bdg-b')+'" style="font-size:9px">'+r.cpg_type+'</span>',
      '<span style="font-family:var(--mono);font-size:10px">'+(r.CUSIP||r.cusip||'\u2014')+'</span>',
      '<span style="font-size:10px">'+(r.FundServ||r.fundserv||'\u2014')+'</span>',
      sf(r.client_rate_pct,2)+'%',
      (r.initial_maturity||'\u2014').slice(0,10),
      (r.final_maturity||'\u2014').slice(0,10),
      fmt(r.PV_total),fmt(r.PV_fixed),fmt(r.option_value),
      r.time_value?fmt(r.time_value):'\u2014',
      '<span style="font-size:9px;color:#8E8E93">'+(r.option_value>0?'HW1F':'DCF')+'</span>'
    ]}));
  h+='</div></div>';
  results.forEach(function(r,i){
    if(!r.exercise_analysis||!r.exercise_analysis.length)return;
    var itm=r.exercise_analysis.filter(function(e){return e.in_the_money}).length;
    h+='<div class="panel block"><div class="panel-header"><div class="dot" style="background:#FF9500"></div>'+r.cpg_type+' '+(r.CUSIP||r.FundServ||'#'+(i+1))+' \u2014 Exercice<span class="bdg bdg-g" style="margin-left:auto">'+itm+'/'+r.exercise_analysis.length+' ITM</span></div><div class="panel-body" style="padding:0;overflow-x:auto">';
    h+=tbl([{t:'Date'},{t:'Strike',r:1,mono:1},{t:'Fwd Fin.',r:1,mono:1},{t:'Moneyness',r:1,mono:1},{t:'Intr. ($)',r:1,mono:1},{t:''}],
      r.exercise_analysis.map(function(e){return[
        e.exercise_date,sf(e.strike_pct,3),sf(e.funding_forward_pct,3),
        '<span class="'+(e.in_the_money?'diff-good':'diff-bad')+'">'+(e.moneyness_bp>=0?'+':'')+sf(e.moneyness_bp,1)+' pb</span>',
        fmt(e.intrinsic_pv),
        '<span class="bdg '+(e.in_the_money?'bdg-g':'bdg-r')+'" style="font-size:8px">'+(e.in_the_money?'ITM':'HEM')+'</span>'+(e.optimal?' \u2605':'')
      ]}));
    h+='</div></div>';
  });
  document.getElementById('resPanel-summary').innerHTML=h;
  document.getElementById('resSubtitle').textContent=results.length+' instruments \u00b7 VA : '+fmt(pvT)+' $';

  /* Fill Greeks tab for multi-deal */
  var vegaT=0;
  results.forEach(function(r){vegaT+=(r.hw_vega_1bp||0)});
  var g2='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px">';
  g2+=mkKpi('Option totale',fmt(optT),'$',results.filter(function(r){return r.option_value>0}).length+' instruments avec option','#FF9500');
  g2+=mkKpi('Intrinsèque',fmt(intrT),'$','','#3A3A3C');
  g2+=mkKpi('Time Value',fmt(timeT),'$','HW1F','#5856D6');
  g2+=mkKpi('Véga HW1F',fmt(vegaT,4),'$/pb','Somme des végas','#FF3B30');
  g2+='</div>';
  g2+='<div class="panel block"><div class="panel-header"><div class="dot"></div>Sensibilités par instrument</div><div class="panel-body" style="padding:0;overflow-x:auto">';
  g2+=tbl([{t:'Type'},{t:'CUSIP'},{t:'Taux',r:1,mono:1},{t:'Option',r:1,mono:1,color:'#FF9500'},{t:'Intrinsèque',r:1,mono:1},{t:'Time Val.',r:1,mono:1,color:'#5856D6'},{t:'Véga',r:1,mono:1,color:'#FF3B30'},{t:'σ (pb)',r:1,mono:1},{t:'Méthode'}],
    results.map(function(r){return[
      '<span class="bdg '+(r.cpg_type==='COUPON'?'bdg-g':'bdg-b')+'" style="font-size:9px">'+r.cpg_type+'</span>',
      '<span style="font-family:var(--mono);font-size:10px">'+(r.CUSIP||r.cusip||'\u2014')+'</span>',
      sf(r.client_rate_pct,2)+'%',
      fmt(r.option_value),fmt(r.intrinsic_value||0),
      r.time_value?fmt(r.time_value):'\u2014',
      r.hw_vega_1bp?fmt(r.hw_vega_1bp,4):'\u2014',
      r.hw_sigma_bp?sf(r.hw_sigma_bp,0):'\u2014',
      '<span style="font-size:9px;color:#8E8E93">'+(r.option_value>0?'HW1F':'DCF')+'</span>'
    ]}));
  g2+='</div></div>';
  g2+='<div style="padding:16px;background:#FFFBF5;border:1px solid #FDE68A;border-radius:10px;font-size:12px;color:#92400E;line-height:1.6">';
  g2+='<strong>Note :</strong> Les DV01, CS01, Gamma, Thêta et scénarios de portefeuille sont disponibles via le mode « Portefeuille existant » (onglet 3 à l\'étape Instruments), qui utilise le pricing DCF agrégé avec les sensibilités complètes. Le mode upload prorogeable calcule les Greeks HW1F (Véga, Time Value) par instrument.</div>';
  document.getElementById('resPanel-greeks').innerHTML=g2;

  /* ═══ EXERCICE TAB — all exercise schedules ═══ */
  var s3='';
  results.forEach(function(r,i){
    var exA=r.exercise_analysis||[];
    if(!exA.length)return;
    var itm=exA.filter(function(e){return e.in_the_money}).length;
    var label=r.cpg_type+' '+(r.CUSIP||r.cusip||r.FundServ||r.fundserv||'#'+(i+1));
    s3+='<div class="panel block"><div class="panel-header"><div class="dot" style="background:#FF9500"></div>'+label+' \u2014 '+sf(r.client_rate_pct,2)+'%<span class="bdg bdg-g" style="margin-left:auto">'+itm+'/'+exA.length+' ITM</span></div><div class="panel-body" style="padding:0;overflow-x:auto">';
    s3+=tbl([{t:'#'},{t:'Date'},{t:'R\u00e9sid.'},{t:'Strike',r:1,mono:1,bold:1},{t:'Fwd OIS',r:1,mono:1},{t:'Spread',r:1,mono:1},{t:'Fwd Fin.',r:1,mono:1,bold:1},{t:'Moneyness',r:1,mono:1},{t:'Intr. ($)',r:1,mono:1},{t:''}],
      exA.map(function(e,j){return[j+1,e.exercise_date,e.residual_days+'j',sf(e.strike_pct,3),sf(e.ois_forward_pct,3),'<span style="color:var(--amber)">'+sf(e.spread_market_pct,3)+'</span>',sf(e.funding_forward_pct,3),'<span class="'+(e.in_the_money?'diff-good':'diff-bad')+'">'+(e.moneyness_bp>=0?'+':'')+sf(e.moneyness_bp,1)+' pb</span>',fmt(e.intrinsic_pv),'<span class="bdg '+(e.in_the_money?'bdg-g':'bdg-r')+'" style="font-size:8px">'+(e.in_the_money?'ITM':'HEM')+'</span>'+(e.optimal?' \u2605':'')]}));
    s3+='</div></div>';
  });
  if(!s3)s3='<div style="padding:40px;text-align:center;color:#8E8E93">Aucun exercice (instruments sans option de prorogation).</div>';
  document.getElementById('resPanel-schedule').innerHTML=s3;

  /* ═══ FLUX TAB — cashflow summary per deal ═══ */
  var s4='<div class="panel block"><div class="panel-header"><div class="dot"></div>Flux de tr\u00e9sorerie par instrument</div><div class="panel-body" style="padding:0;overflow-x:auto">';
  s4+=tbl([{t:'Type'},{t:'CUSIP'},{t:'Taux',r:1,mono:1},{t:'P\u00e9riode garantie'},{t:'VA Fixe',r:1,mono:1,bold:1,color:'var(--green)'},{t:'Nominal',r:1,mono:1},{t:'Ann\u00e9es',r:1,mono:1}],
    results.map(function(r){
      var yrs=r.min_years?sf(r.min_years,0)+'\u2013'+sf(r.max_years,0)+'A':'\u2014';
      return[
        '<span class="bdg '+(r.cpg_type==='COUPON'?'bdg-g':'bdg-b')+'" style="font-size:9px">'+r.cpg_type+'</span>',
        '<span style="font-family:var(--mono);font-size:10px">'+(r.CUSIP||r.cusip||'\u2014')+'</span>',
        sf(r.client_rate_pct,2)+'%',
        (r.initial_maturity||'').slice(0,10)+' \u2192 '+(r.final_maturity||'').slice(0,10),
        fmt(r.PV_fixed),fmt(r.PV_total-r.PV_fixed>0?r.PV_total:r.PV_fixed),yrs
      ]}));
  s4+='</div></div>';
  s4+='<div style="padding:14px;background:var(--bg3);border-radius:10px;font-size:12px;color:#636366;line-height:1.6">Les flux d\u00e9taill\u00e9s (coupon par coupon) sont disponibles en saisie manuelle (un seul instrument).</div>';
  document.getElementById('resPanel-cashflows').innerHTML=s4;

  /* ═══ COURBE TAB ═══ */
  var s5='<div style="padding:40px;text-align:center;color:#8E8E93"><p style="font-size:14px;font-weight:600;margin-bottom:8px">Courbe de march\u00e9</p><p>La courbe utilis\u00e9e est la m\u00eame pour tous les instruments.</p><p style="margin-top:8px">Consultez l\'\u00e9tape 1 (Donn\u00e9es de march\u00e9) pour l\'aper\u00e7u de la courbe.</p></div>';
  document.getElementById('resPanel-curve').innerHTML=s5;

  /* ═══ MODÈLE TAB ═══ */
  var s6='<div class="panel block"><div class="panel-header"><div class="dot"></div>Param\u00e8tres du mod\u00e8le</div><div class="panel-body"><div class="dgrid" style="grid-template-columns:repeat(3,1fr)">';
  s6+=dc('Mod\u00e8le','Hull-White 1-Factor');s6+=dc('Moteur','Arbre trinomial');s6+=dc('Calibration','Structure \u00e0 terme initiale');
  s6+=dc('R\u00e9tro. moyenne',sf(results[0].hw_mean_reversion||0.03,3));s6+=dc('\u03c3 vol (pb)',sf(results[0].hw_sigma_bp||65,0)+' pb');s6+=dc('Spread','D\u00e9terministe terme-d\u00e9pendant');
  s6+='</div></div></div>';
  s6+='<div class="panel block"><div class="panel-header"><div class="dot"></div>Spread par instrument</div><div class="panel-body" style="padding:0;overflow-x:auto">';
  s6+=tbl([{t:'Type'},{t:'CUSIP'},{t:'Spread march\u00e9 (pb)',r:1,mono:1,bold:1},{t:'Exercices',r:1}],
    results.map(function(r){return[
      r.cpg_type,(r.CUSIP||r.cusip||'\u2014'),sf(r.spread_market_flat_bp,1),r.n_exercise_dates||0
    ]}));
  s6+='</div></div>';
  document.getElementById('resPanel-calib').innerHTML=s6;

  /* ═══ VOL TAB ═══ */
  var s7='<div style="padding:40px;text-align:center;color:#8E8E93"><p style="font-size:14px;font-weight:600;margin-bottom:8px">Surface de volatilit\u00e9</p><p>Vol normale utilis\u00e9e : '+sf(results[0].hw_sigma_bp||65,0)+' pb (proxy param\u00e9trique).</p><p style="margin-top:8px">Consultez l\'\u00e9tape 1 pour la surface compl\u00e8te.</p></div>';
  document.getElementById('resPanel-vol').innerHTML=s7;

  /* ═══ RISQUE CLÉ TAB ═══ */
  var s8='<div class="panel block"><div class="panel-header"><div class="dot" style="background:#FF3B30"></div>V\u00e9ga par instrument (sensibilit\u00e9 \u00e0 la vol)</div><div class="panel-body" style="padding:0;overflow-x:auto">';
  var vegaList=results.filter(function(r){return r.hw_vega_1bp&&r.hw_vega_1bp>0});
  if(vegaList.length){
    s8+=tbl([{t:'Type'},{t:'CUSIP'},{t:'Taux',r:1,mono:1},{t:'Option',r:1,mono:1,color:'#FF9500'},{t:'V\u00e9ga ($/pb)',r:1,mono:1,bold:1,color:'#FF3B30'},{t:'\u03c3',r:1,mono:1}],
      vegaList.map(function(r){return[r.cpg_type,(r.CUSIP||r.cusip||'\u2014'),sf(r.client_rate_pct,2)+'%',fmt(r.option_value),fmt(r.hw_vega_1bp,4),sf(r.hw_sigma_bp,0)+' pb']}));
  } else {s8+='<div style="padding:20px;text-align:center;color:#8E8E93">Aucun instrument avec option (V\u00e9ga = 0).</div>'}
  s8+='</div></div>';
  s8+='<div style="padding:14px;background:var(--bg3);border-radius:10px;font-size:12px;color:#636366;line-height:1.6">L\'analyse KR-DV01 compl\u00e8te (par point de courbe) est disponible via le mode \u00ab Portefeuille existant \u00bb.</div>';
  document.getElementById('resPanel-krr').innerHTML=s8;

  /* ═══ SCÉNARIOS TAB ═══ */
  var s9='<div style="padding:40px;text-align:center;color:#8E8E93"><p style="font-size:14px;font-weight:600;margin-bottom:8px">Sc\u00e9narios de taux</p><p>Les sc\u00e9narios (parall\u00e8les, twist, etc.) sont disponibles via le mode \u00ab Portefeuille existant \u00bb qui utilise le pricing DCF agr\u00e9g\u00e9.</p></div>';
  document.getElementById('resPanel-scenarios').innerHTML=s9;
}
function renderPortfolioResults(d){var ok=d.results.filter(function(r){return r.Status==='OK'}),tot=ok.reduce(function(s,r){return s+(r.PV||0)},0),notl=ok.reduce(function(s,r){return s+(r.Montant||0)},0);var h='<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:18px">';h+=mkKpi('VA Totale',fmtM(tot),'CAD',ok.length+' instruments','var(--green)');h+=mkKpi('VA Coupons',fmtM(ok.reduce(function(s,r){return s+(r.PV_Coupons||0)},0)),'CAD','','#007AFF');h+=mkKpi('VA Capital',fmtM(ok.reduce(function(s,r){return s+(r.PV_Principal||0)},0)),'CAD','','#5856D6');h+=mkKpi('VA/Nominal',sf(tot/notl*100,2)+' %','','','#3A3A3C');h+=mkKpi('Duration',sf(d.avg_duration,2),'ans','','#FF9500');h+='</div>';
h+='<div class="panel block"><div class="panel-header"><div class="dot"></div>Détail</div><div class="panel-body" style="padding:0;overflow-x:auto">';h+=tbl([{t:'CUSIP'},{t:'Type'},{t:'Montant',r:1,mono:1},{t:'Coupon',r:1,mono:1},{t:'VA',r:1,mono:1,bold:1,color:'var(--green)'},{t:'FA',r:1,mono:1},{t:'Duration',r:1,mono:1},{t:''}],ok.map(function(r){return[r.CUSIP||'—','<span class="bdg '+(r.CodeTransaction==='COUPON'?'bdg-g':'bdg-b')+'" style="font-size:9px">'+r.CodeTransaction+'</span>',fmtM(r.Montant||0),sf(r.Coupon,2)+'%',fmt(r.PV,2),sf(r.DF_Maturity,6),sf(r.Duration,2),'<span class="bdg bdg-g" style="font-size:9px">OK</span>']}));
h+='</div></div><div id="greeksArea" style="margin-top:18px"><div style="text-align:center;padding:20px;color:#8E8E93"><div style="width:20px;height:20px;border:2px solid #E5E5EA;border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite;display:inline-block;margin-right:8px;vertical-align:middle"></div>Calcul des sensibilités...</div></div>';
document.getElementById('resPanel-summary').innerHTML=h;document.getElementById('resSubtitle').textContent=ok.length+' trades · VA : '+fmtM(tot)+' CAD';showRes('summary')}

/* ═══ GREEKS FETCH ═══ */
function fetchAndAppendGreeks(){var ev=document.getElementById('evalDate').value;fetch('/cpg/api/greeks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({eval_date:ev,bump_bp:1.0})}).then(function(r){return r.json()}).then(function(g){if(!g.error)appendGreeksToSummary(g)}).catch(function(){})}
function appendGreeksToSummary(g){var h='<div style="margin-top:18px;font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#8E8E93;margin-bottom:8px">Analyse de risque</div>';
h+='<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:18px">';h+=mkKpi('DV01',fmt(g.dv01.DV01),'$/pb','','#FF3B30');h+=mkKpi('CS01',fmt(g.cs01?g.cs01.CS01:0),'$/pb','','#FF9500');h+=mkKpi('Gamma',fmt(g.gamma.Gamma_1bp,4),'$/pb²','','#5856D6');h+=mkKpi('Thêta',fmt(g.theta.Theta_1d),'$','','#007AFF');h+=mkKpi('Véga',fmt(g.vega.Vega_1bp),'$',g.vega.source||'','#3A3A3C');h+=mkKpi('Portage',sf(g.theta.carry_bps,2),'pb/j','','#636366');h+='</div>';
var totKR=Object.values(g.key_rate_dv01).reduce(function(s,v){return s+Math.abs(v)},0);
h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px"><div class="panel"><div class="panel-header"><div class="dot" style="background:#FF3B30"></div>DV01 par point clé</div><div class="panel-body" style="padding:0">';
h+=tbl([{t:'Point'},{t:'DV01',r:1,mono:1,bold:1},{t:'%',r:1},{t:''}],Object.entries(g.key_rate_dv01).filter(function(e){return Math.abs(e[1])>0.001}).map(function(e){var k=e[0],v=e[1];return[k,fmt(v),sf(totKR>0?Math.abs(v)/totKR*100:0,1)+'%','<div class="krbar"><div class="krbar-f" style="width:'+Math.min(totKR>0?Math.abs(v)/totKR*100:0,100)+'%"></div></div>']}));
h+='</div></div><div class="panel"><div class="panel-header"><div class="dot" style="background:#FF3B30"></div>Distribution</div><div class="panel-body" style="padding:8px"><canvas id="krCP" height="200" style="width:100%"></canvas></div></div></div>';
h+='<div class="panel"><div class="panel-header"><div class="dot" style="background:var(--amber)"></div>Scénarios</div><div class="panel-body" style="padding:0">';
h+=tbl([{t:'Scénario'},{t:'VA',r:1,mono:1,bold:1},{t:'ΔVA',r:1,mono:1},{t:'Δ%',r:1,mono:1}],g.scenarios.map(function(s){return[s.scenario,fmt(s.PV),'<span class="'+(s.delta_PV>0?'diff-good':'diff-bad')+'">'+(s.delta_PV>=0?'+':'')+fmt(s.delta_PV)+'</span>',sf(s.delta_pct,3)+'%']}));h+='</div></div>';
var area=document.getElementById('greeksArea');if(area)area.innerHTML=h;
var kv=Object.entries(g.key_rate_dv01).filter(function(e){return Math.abs(e[1])>0.001});
_chartData.krPortfolio={vals:kv.map(function(e){return e[1]}),labels:kv.map(function(e){return e[0]})};
setTimeout(function(){drawBarChart('krCP',_chartData.krPortfolio.vals,_chartData.krPortfolio.labels,'$/pb',200)},100)}

/* ═══ CHARTS ═══ */
function _drawDeferredCharts(tabId){if(tabId==='schedule'&&_chartData.moneyness)drawBarChart('moneyC',_chartData.moneyness.vals,_chartData.moneyness.labels,'pb',160);if(tabId==='krr'&&_chartData.krr)drawBarChart('krC2',_chartData.krr.vals,_chartData.krr.labels,'$/pb',200)}
function drawBarChart(cid,vals,labels,unit,H){var c=document.getElementById(cid);if(!c||!vals||!vals.length)return;var pw=c.parentElement.offsetWidth;if(pw<50){setTimeout(function(){drawBarChart(cid,vals,labels,unit,H)},120);return}var ctx=c.getContext('2d'),W=pw-16;H=H||160;c.width=W*2;c.height=H*2;c.style.width=W+'px';c.style.height=H+'px';ctx.setTransform(1,0,0,1,0,0);ctx.scale(2,2);ctx.clearRect(0,0,W,H);var pad={t:14,r:14,b:28,l:50},ppw=W-pad.l-pad.r,ph=H-pad.t-pad.b,mx=Math.max.apply(null,vals.map(Math.abs))*1.2;if(!mx||!isFinite(mx))mx=1;var hasN=vals.some(function(v){return v<0}),bW=Math.max(Math.min(ppw/vals.length*.7,28),4),gap=(ppw-bW*vals.length)/(vals.length+1);ctx.fillStyle='#fff';ctx.fillRect(0,0,W,H);var y0=hasN?pad.t+ph/2:pad.t+ph;if(hasN){ctx.strokeStyle='#E5E5EA';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad.l,y0);ctx.lineTo(W-pad.r,y0);ctx.stroke()}vals.forEach(function(v,i){if(!isFinite(v))return;var x=pad.l+gap+(bW+gap)*i,bH=hasN?Math.abs(v)/mx*(ph/2):Math.abs(v)/mx*ph;if(bH<1)bH=1;var y=v>=0?y0-bH:y0;ctx.fillStyle=v>=0?'#00874E':'#FF3B30';ctx.beginPath();ctx.roundRect(x,y,bW,bH,[3,3,0,0]);ctx.fill();ctx.fillStyle=v>=0?'#00874E':'#FF3B30';ctx.font='bold 8px system-ui';ctx.textAlign='center';var vT=Math.abs(v)>=100?v.toFixed(0):Math.abs(v)>=1?v.toFixed(1):v.toFixed(2);if(v>=0&&hasN)vT='+'+vT;ctx.fillText(vT,x+bW/2,v>=0?y-3:y+bH+9);if(labels&&labels[i]){ctx.fillStyle='#8E8E93';ctx.font='7px system-ui';ctx.save();ctx.translate(x+bW/2,H-pad.b+10);if(vals.length>6)ctx.rotate(-.3);ctx.fillText(labels[i],0,0);ctx.restore()}})}

function exportResults(){
  if(!pricingDone){alert('Lancez le pricing d\'abord.');return}
  var a=document.createElement('a');a.href='/cpg/api/export';a.download='resultats_epargne_terme.xlsx';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
}

/* ═══ HISTORIQUE ═══ */
var HIST_KEY='cpg_pricing_history';
function getHistory(){try{return JSON.parse(localStorage.getItem(HIST_KEY)||'[]')}catch(e){return[]}}
function saveHistory(entry){var h=getHistory();h.unshift(entry);if(h.length>100)h.length=100;localStorage.setItem(HIST_KEY,JSON.stringify(h))}
function clearHistory(){if(!confirm('Supprimer tout l\'historique ?'))return;localStorage.removeItem(HIST_KEY);renderHistory()}
function deleteHistEntry(idx){var h=getHistory();h.splice(idx,1);localStorage.setItem(HIST_KEY,JSON.stringify(h));renderHistory()}
function saveRun(params,result){saveHistory({ts:new Date().toISOString(),eval_date:document.getElementById('evalDate').value,mode:dealMode,type:params.cpg_type||params.type||'PORTEFEUILLE',fundserv:params.fundserv||'',notional:params.notional||0,rate:params.client_rate||0,pv:result.PV_total||(result.valuation&&result.valuation.npv)||0,option:result.option_value||(result.valuation&&result.valuation.option_value)||0,n_instruments:result.n||1})}
function renderHistory(){var h=getHistory();document.getElementById('histCount').textContent=h.length+' session'+(h.length!==1?'s':'');if(!h.length){document.getElementById('histBody').innerHTML='<div style="padding:40px;color:#8E8E93;font-size:13px;text-align:center">Aucune session enregistrée.<br><span style="font-size:12px">Les résultats de pricing seront sauvegardés automatiquement.</span></div>';return}
var html='<table class="results-table"><thead><tr><th>Date</th><th>Heure</th><th>Type</th><th>FundServ</th><th style="text-align:right">Nominal</th><th style="text-align:right">Taux</th><th style="text-align:right">VA</th><th style="text-align:right">Option</th><th></th></tr></thead><tbody>';
h.forEach(function(r,i){var d=new Date(r.ts);html+='<tr><td style="font-size:10px;color:#8E8E93">'+d.toLocaleDateString('fr-CA')+'</td><td style="font-size:10px;color:#8E8E93">'+d.toLocaleTimeString('fr-CA',{hour:'2-digit',minute:'2-digit'})+'</td>';html+='<td><span class="bdg '+(r.type==='COUPON'?'bdg-g':r.type==='ACCUMULATION LINÉAIRE'||r.type==='LINEAR ACCRUAL'?'bdg-b':'bdg-d')+'" style="font-size:9px">'+r.type+'</span></td>';html+='<td style="font-family:var(--mono);font-size:10px">'+(r.fundserv||'—')+'</td>';html+='<td style="text-align:right;font-family:var(--mono)">'+fmtM(r.notional||0)+'</td>';html+='<td style="text-align:right;font-family:var(--mono)">'+sf(r.rate,2)+'%</td>';html+='<td style="text-align:right;font-family:var(--mono);font-weight:600;color:var(--green)">'+fmt(r.pv)+'</td>';html+='<td style="text-align:right;font-family:var(--mono);color:#FF9500">'+(r.option?fmt(r.option):'—')+'</td>';html+='<td style="text-align:right"><button onclick="reloadRun('+i+')" style="font-size:9px;color:#007AFF;background:none;border:none;cursor:pointer;font-weight:600;padding:2px 6px">Recharger</button><button onclick="deleteHistEntry('+i+')" style="font-size:9px;color:#FF3B30;background:none;border:none;cursor:pointer;padding:2px 4px">✕</button></td></tr>'});html+='</tbody></table>';document.getElementById('histBody').innerHTML=html}
function reloadRun(idx){var h=getHistory();if(idx>=h.length)return;var r=h[idx];document.getElementById('evalDate').value=r.eval_date||'2026-02-26';if(r.type&&r.type!=='PORTEFEUILLE'){document.getElementById('extType').value=r.type==='ACCUMULATION LINÉAIRE'||r.type==='LINEAR ACCRUAL'?'LINEAR ACCRUAL':'COUPON';document.getElementById('extFund').value=r.fundserv||'';document.getElementById('extNot').value=r.notional||10000;document.getElementById('extRate').value=r.rate||4.10;extTypeChanged();document.getElementById('extRate').value=r.rate||4.10;document.getElementById('extFund').value=r.fundserv||''}goPage('pricer');goStep(2)}
function exportHistory(){var h=getHistory();if(!h.length)return;var csv='Date,Type,FundServ,Nominal,Taux,VA,Option\n';h.forEach(function(r){csv+=new Date(r.ts).toLocaleDateString('fr-CA')+','+r.type+','+(r.fundserv||'')+','+(r.notional||0)+','+(r.rate||0)+','+(r.pv||0)+','+(r.option||0)+'\n'});var blob=new Blob([csv],{type:'text/csv'});var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='historique_cpg_'+new Date().toISOString().slice(0,10)+'.csv';a.click()}

/* ═══ INIT ═══ */
/* Date d'évaluation = veille (T-1) par défaut */
(function(){var d=new Date();d.setDate(d.getDate()-1);while(d.getDay()===0||d.getDay()===6)d.setDate(d.getDate()-1);var y=d.getFullYear(),m=String(d.getMonth()+1).padStart(2,'0'),dd=String(d.getDate()).padStart(2,'0');document.getElementById('evalDate').value=y+'-'+m+'-'+dd;document.getElementById('footEval').textContent='Évaluation : '+y+'-'+m+'-'+dd})();
goPage('pricer');goStep(1);setTimeout(applyVolProxy,200);updateDealPreview();
