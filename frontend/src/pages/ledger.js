import {state} from '../stores/session.js';import {esc} from '../utils/html.js';import {field,select} from '../components/common/fields.js';import {PARTS} from '../constants/enums.js';

const STATE_LABEL={manual:'手工用件',open:'核算中',settled:'已结清'};
const ENTRY_LABEL={issue:'领用',return:'退料'};
const STATUS_LABEL={booked:'已入账',pending:'暂挂',rejected:'拒绝'};
const stateTag=s=>`<span class="tag">${esc(STATE_LABEL[s]||s)}</span>`;
const statusTag=s=>`<span class="tag">${esc(STATUS_LABEL[s]||s)}</span>`;
const rawTable=(headers,rows)=>`<div class="scroll"><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(v=>`<td>${v}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="${headers.length}">暂无记录。</td></tr>`}</tbody></table></div>`;

export function render(){
 const d=state.ledgerDetail, job=d?.job;
 const jobOptions=state.jobs.map(j=>[j.code,`${j.code} · ${j.equipment} · ${STATE_LABEL[j.ledger_state]||j.ledger_state}`]);
 const detail=d?`
  <h3>${esc(job.code)} ${stateTag(job.ledger_state)}</h3>
  <p>设备 ${esc(job.equipment)} · 当前登记用件 ${esc((job.parts||[]).join(' / ')||'无')}${job.settled_at?` · 结清于 ${esc(job.settled_at)}`:''}</p>
  <div class="metrics"><div><strong>${d.counts.booked}</strong>已入账</div><div><strong>${d.counts.pending}</strong>暂挂</div><div><strong>${d.counts.rejected}</strong>拒绝</div></div>
  <h4>逐件对账</h4>
  ${rawTable(['备件','领用','已退','净用量','暂挂笔数','拒绝笔数','进入新挖掘'],d.summary.map(s=>[esc(s.part),s.issued,s.returned,s.net,s.pending,s.rejected,s.in_mining?'是':'否']))}
  <h4>领用与退料对照</h4>
  ${d.issues.length?rawTable(['领用流水','备件','领用','已退','可退余额','引用它的退料'],d.issues.map(i=>[esc(i.entry_no),esc(i.part),i.qty,i.returned,i.remaining,i.returns.map(r=>`${esc(r.entry_no)}×${r.qty} ${esc(STATUS_LABEL[r.status]||r.status)}`).join('<br>')||'—'])):'<p class="notice">该作业还没有已入账的领用流水。</p>'}
  <h4>流水明细</h4>
  ${rawTable(['流水号','方向','备件','数量','引用','状态','说明','操作'],d.entries.map(e=>[esc(e.entry_no),esc(ENTRY_LABEL[e.direction]||e.direction),esc(e.part),e.qty,esc(e.ref_entry_no||'—'),statusTag(e.status),esc(e.reject_reason||'—'),e.status==='pending'?`<button class="mini" data-void="${esc(e.entry_no)}">撤回暂挂</button>`:'']))}
  <div class="actions">
   <button data-settle="${esc(job.code)}" ${job.ledger_state!=='open'?'disabled':''}>结清作业</button>
   <button data-reopen="${esc(job.code)}" ${job.ledger_state!=='settled'?'disabled':''}>重开核算</button>
  </div>
  <p class="notice">结清只把净用量为正的件写入作业用件；核算中作业排除在新挖掘之外。历史批次的快照不会因结清或补料改变。</p>`
  :'<p class="notice">请选择一张作业。</p>';

 return `<div class="split"><section class="panel"><h2>领退流水与逐件对账</h2><p>每笔流水有唯一流水号；退料引用原领用，原领用未到时暂挂，到齐后补算。跨作业引用与累计超退拒绝入账。</p>${detail}<h3>最近流水（全部作业）</h3>${rawTable(['流水号','作业','方向','备件','数量','引用','状态'],state.ledger.map(e=>[esc(e.entry_no),esc(e.job_code),esc(ENTRY_LABEL[e.direction]||e.direction),esc(e.part),e.qty,esc(e.ref_entry_no||'—'),statusTag(e.status)]))}</section>
 <aside class="side"><h2>登记流水</h2><form id="ledger">${select('作业','job_code',jobOptions.length?jobOptions:[['','暂无作业']])}${field('流水号','entry_no','text','required placeholder=I-1115-001')}${select('方向','direction',[['issue','领用'],['return','退料']])}${select('备件','part',PARTS.map(p=>[p,p]))}${field('数量','qty','number','min=1 step=1 required value=1')}${field('引用原领用流水号（退料必填）','ref_entry_no','text','placeholder=I-1115-001')}<p>领用不得填写引用；退料必须引用同一作业、同一备件的原领用。同一流水号重试不重复记账，内容不同则报冲突。</p><button class="primary">提交流水</button></form>
 <h3>结清与补料</h3><p>存在暂挂退料时不能结清；结清后新增流水须先重开核算。结清后补料重开，再结清时重新计算净用量。</p></aside></div>`;
}
