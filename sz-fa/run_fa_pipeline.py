#!/usr/bin/env python3
"""Run V3 upstream stages followed by speech-routed MFA/Qwen FA."""
import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PARENT=ROOT.parent
PIPELINE_PYTHON=Path(os.environ.get('PIPELINE_PYTHON','python3'))
MFA_PYTHON=Path(os.environ.get('MFA_PYTHON',str(ROOT/'models/mfa/bin/python')))
QWEN_PYTHON_DEFAULT=Path(os.environ.get(
    'QWEN_PYTHON', '/hbt_share02/202610/ob/ob_ppl_env_data/envs/qwen3-asr/bin/python'))
if not QWEN_PYTHON_DEFAULT.exists():
 QWEN_PYTHON_DEFAULT=PIPELINE_PYTHON
STAGES=('ingest','dnsmos','vad','enhance','asr1','asr2','disagreement','asr3','rover')
def dump(p,v): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def logged(cmd,log,env):
 t=time.perf_counter()
 with log.open('w') as s:
  s.write('$ '+' '.join(map(str,cmd))+'\n\n'); s.flush(); r=subprocess.run([str(x) for x in cmd],stdout=s,stderr=subprocess.STDOUT,env=env)
 return r.returncode,time.perf_counter()-t
def reports(root,dataset):
 runroot=root/'production'/dataset/'runs'; out={}
 if not runroot.exists(): return []
 for run in sorted((p for p in runroot.iterdir() if p.is_dir()),key=lambda p:p.stat().st_mtime):
  for p in run.glob('*-report.json'):
   try: x=json.loads(p.read_text())
   except Exception: continue
   out[x.get('stage',p.stem.replace('-report',''))]=x
 return [out[x] for x in STAGES if x in out]
def main():
 p=argparse.ArgumentParser(); p.add_argument('--dataset',default='oto_141'); p.add_argument('--output-root',type=Path,default=ROOT/'runs'/'oto141_speech_routed_fa'); p.add_argument('--skip-upstream',action='store_true'); p.add_argument('--mfa-jobs',type=int,default=32); p.add_argument('--mfa-timeout',type=int,default=14400); p.add_argument('--gpus',default='0,1,2,3'); p.add_argument('--resources',type=Path,default=PARENT/'config/resources.v3.oto104_magiczh.yaml'); p.add_argument('--mapping',type=Path,default=Path('/hbt_share02/202610/ob/ob_ppl_env_data/dataset/mapping_final/Mapping_with_rover/oto_141_with_rover_manifest.json')); p.add_argument('--english-backchannel-map',type=Path,default=PARENT/'mapping_flexible_v2.json'); p.add_argument('--qwen-checkpoint',type=Path,default=PARENT/'models/qwen3-asr/checkpoints/Qwen3-ForcedAligner-0.6B'); p.add_argument('--qwen-python',type=Path,default=QWEN_PYTHON_DEFAULT); p.add_argument('--qwen-device',default='cuda:0'); p.add_argument('--qwen-batch-size',type=int,default=4); p.add_argument('--asr-rerun-checkpoint',type=Path,default=PARENT/'models/qwen3-asr/checkpoints/Qwen3-ASR-1.7B'); p.add_argument('--asr-rerun-python',type=Path,default=QWEN_PYTHON_DEFAULT); p.add_argument('--skip-messy-rerun',action='store_true'); a=p.parse_args()
 output=a.output_root.resolve(); output.mkdir(parents=True,exist_ok=True); started=time.perf_counter(); env=dict(os.environ,CUDA_VISIBLE_DEVICES='',TOKENIZERS_PARALLELISM='false'); commands=[]; timings={}
 upstream=[PIPELINE_PYTHON,PARENT/'run.py',*STAGES,'--dataset',a.dataset,'--config',PARENT/'config/pipeline.v3.oto104_magiczh.yaml','--resources',a.resources,'--datasets',PARENT/'config/datasets.v3.oto104_magiczh.local.yaml','--output-root',output/'upstream_output','--gpus',a.gpus,'--no-cache','--resume-validation','full']; commands.append([str(x) for x in upstream])
 if not a.skip_upstream:
  code,sec=logged(upstream,output/'upstream_pipeline.log',env); timings['upstream_seconds']=sec
  if code: raise SystemExit(f'upstream pipeline failed with exit code {code}')
  upstream_state={'returncode':code,'seconds':sec,'command':commands[-1]}
 else: upstream_state={'skipped':True,'command':commands[-1]}
 rover=output/'upstream_output'/'production'/a.dataset/'rover'/'recordings'
 if not rover.exists(): raise FileNotFoundError(f'ROVER recording JSON directory missing: {rover}')
 speech_output=output/'speech_fa'
 speech_cmd=[PIPELINE_PYTHON,ROOT/'src/process_speeches.py','--recordings',rover,'--mapping',a.mapping,'--output',speech_output,'--english-backchannel-map',a.english_backchannel_map]
 commands.append([str(x) for x in speech_cmd]); code,sec=logged(speech_cmd,output/'speech_fa.log',env); timings['speech_fa_seconds']=sec
 if code: raise SystemExit(f'speech-level FA failed with exit code {code}; see {output}/speech_fa.log')
 rerun_input=speech_output/'rerun_inputs.jsonl'; rerun_result=speech_output/'messy_asr_predictions.jsonl'
 if rerun_input.exists() and rerun_input.stat().st_size and not a.skip_messy_rerun:
  rerun_cmd=[a.asr_rerun_python,ROOT/'src/messy_asr_rerun.py','--input',rerun_input,'--output',rerun_result,'--checkpoint',a.asr_rerun_checkpoint,'--device',a.qwen_device,'--batch-size',str(a.qwen_batch_size)]
  commands.append([str(x) for x in rerun_cmd]); code,sec=logged(rerun_cmd,output/'messy_asr_rerun.log',dict(env,CUDA_VISIBLE_DEVICES=a.gpus)); timings['messy_asr_rerun_seconds']=sec
  if code: raise SystemExit(f'messy ASR rerun failed with exit code {code}; see {output}/messy_asr_rerun.log')
  speech_cmd_rerun=speech_cmd+['--rerun-results',rerun_result]; commands.append([str(x) for x in speech_cmd_rerun]); code,sec=logged(speech_cmd_rerun,output/'speech_fa_after_rerun.log',env); timings['speech_fa_after_rerun_seconds']=sec
  if code: raise SystemExit(f'speech archive rebuild after messy rerun failed with exit code {code}')
 speech_archive= speech_output/'speech_archive.json'
 route_dir=speech_output/'route_inputs'
 pure=output/'pure_mfa'; mfa_inputs=list((route_dir/'mfa_recordings').glob('*.json'))
 if mfa_inputs:
  cmd=[PIPELINE_PYTHON,ROOT/'src/run_recordings_mfa.py','--recordings',route_dir/'mfa_recordings','--output',pure,'--mfa-jobs',str(a.mfa_jobs),'--mfa-python',MFA_PYTHON,'--mfa-timeout',str(a.mfa_timeout)]; commands.append([str(x) for x in cmd]); code,sec=logged(cmd,output/'pure_mfa.log',env); timings['pure_mfa_seconds']=sec
  if code: raise SystemExit(f'pure MFA failed with exit code {code}; see {output}/pure_mfa.log')
 else:
  pure.mkdir(parents=True,exist_ok=True); dump(pure/'summary.json',dict(method='mfa',device='cpu',complete=True,totals=dict(chunks=0,words=0,failed_chunks=0)))
 qwen=None
 qwen_input=route_dir/'qwen_fa_input.jsonl'
 if a.qwen_checkpoint and qwen_input.stat().st_size:
  qwen=output/'qwen_fa'; qcmd=[a.qwen_python,ROOT/'src/qwen3-fa/process.py','--input',qwen_input,'--output',qwen,'--checkpoint',a.qwen_checkpoint,'--device',a.qwen_device,'--batch-size',str(a.qwen_batch_size),'--export-unit','both']; commands.append([str(x) for x in qcmd]); qwen_env=dict(env, CUDA_VISIBLE_DEVICES=a.gpus); code,sec=logged(qcmd,output/'qwen_fa.log',qwen_env); timings['qwen_fa_seconds']=sec
  if code: raise SystemExit(f'qwen FA failed with exit code {code}; see {output}/qwen_fa.log')
 qwen_predictions = qwen/'alignment_predictions.jsonl' if qwen else None
 reconciled=output/'speech_fa_reconciled'; rec_cmd=[PIPELINE_PYTHON,ROOT/'src/reconcile_mfa.py','--speech-archive',speech_archive,'--mfa-output',pure,'--output',reconciled]
 if qwen_predictions: rec_cmd.extend(['--qwen-output',qwen_predictions])
 commands.append([str(x) for x in rec_cmd]); code,sec=logged(rec_cmd,output/'mfa_reconcile.log',env); timings['mfa_reconcile_seconds']=sec
 if code: raise SystemExit(f'FA reconciliation failed with exit code {code}; see {output}/mfa_reconcile.log')
 clean_dir=output/'fa_data'; anomaly_dir=output/'fa_anomalies'
 anomaly_cmd=[PIPELINE_PYTHON,ROOT/'src/anomaly_filter.py','--speech-archive',reconciled/'speech_archive.json','--background-audio',reconciled/'background_audio.json','--failure-speeches',reconciled/'failure_speeches.json','--output-data',clean_dir,'--output-anomalies',anomaly_dir]
 if qwen_predictions: anomaly_cmd.extend(['--qwen-predictions',qwen_predictions])
 commands.append([str(x) for x in anomaly_cmd]); code,sec=logged(anomaly_cmd,output/'anomaly_filter.log',env); timings['anomaly_filter_seconds']=sec
 if code: raise SystemExit(f'anomaly filtering failed with exit code {code}; see {output}/anomaly_filter.log')
 anomalies=json.loads((anomaly_dir/'anomaly_summary.json').read_text())
 mfa=json.loads((pure/'summary.json').read_text()); speech=json.loads((speech_output/'summary.json').read_text()); reconciled_summary=json.loads((reconciled/'mfa_reconciliation_summary.json').read_text())
 for key in ('qwen_fa_zero_duration_word_rate','qwen_fa_zero_duration_words','qwen_fa_total_words','zero_duration_words','total_words','zero_duration_chunk_rate','zero_duration_speech_rate','background_audio_chunks','failure_speeches'):
  if key in reconciled_summary: speech[key]=reconciled_summary[key]
 dump(speech_output/'summary.json',speech)
 qwen_summary=json.loads((qwen/'error_stats.json').read_text()) if qwen and (qwen/'error_stats.json').exists() else None
 report={'status':'complete','method':'speech_routed_fa','dataset':a.dataset,'source_root':str(PARENT),'output_root':str(output),'gpu_count':len([x for x in a.gpus.split(',') if x.strip()]),'gpus':a.gpus,'mfa_jobs':a.mfa_jobs,'timings_seconds':timings,'total_seconds':time.perf_counter()-started,'upstream':upstream_state,'upstream_stage_reports':reports(output/'upstream_output',a.dataset),'speech_fa':speech,'pure_mfa':mfa,'mfa_reconciliation':reconciled_summary,'qwen_fa':qwen_summary,'anomalies':anomalies,'clean_data_dir':str(clean_dir),'anomaly_data_dir':str(anomaly_dir),'commands':commands,'code_sha256':hashlib.sha256((ROOT/'src/process_speeches.py').read_bytes()).hexdigest()}; dump(output/'fa_report.json',report); dump(output/'call_stack.json',{'commands':commands,'stages':['run_fa_pipeline.py','parent/run.py -> upstream nine stages','ROVER recordings -> src/process_speeches.py -> one speech JSON with two speaker chunk lists','route_inputs/mfa_recordings -> src/run_recordings_mfa.py -> src/mfa/process.py','route_inputs/qwen_fa_input.jsonl -> src/qwen3-fa/process.py (optional)','reconcile_mfa.py -> background_audio.json + filtered speech_archive.json','anomaly_filter.py -> fa_data or fa_anomalies (whole speech JSON)','fa_report.json'],'python':{'pipeline':str(PIPELINE_PYTHON),'mfa':str(MFA_PYTHON),'qwen':str(a.qwen_python),'version':sys.version}}); print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
