/**
 * @typedef {{id:number,code:string,equipment:string,parts:string[],note:string,ledger_state:'manual'|'open'|'settled',settled_at:string|null}} Job
 * @typedef {{id:number,name:string,state:string,result:object}} MiningRun
 * @typedef {{id:number,run_id:number,antecedent:string[],consequent:string[],support:number,confidence:number,lift:number}} AssociationRule
 * @typedef {{id:number,name:string,rule_id:number,parts:string[],note:string,status:string}} Kit
 * @typedef {{id:number,entry_no:string,job_id:number,direction:'issue'|'return',part:string,qty:number,returned_qty:number,ref_entry_no:string|null,status:'booked'|'pending'|'rejected',reject_reason:string,created_by:string,created_at:string}} LedgerEntry
 * @typedef {{job:Job,entries:LedgerEntry[],summary:object[],issues:object[],counts:{booked:number,pending:number,rejected:number}}} LedgerDetail
 */
