"""Refresh this compact evidence bundle from an existing completed/active job.

Usage: python3 collect_evidence.py /path/to/local/Pam/job
Reads only compact JSON/PNG/scripts, never meshes or G-code. Local absolute
paths are replaced by artifact basenames in public records. No source is edited.
"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

HERE=Path(__file__).resolve().parent
LIMIT_BYTES=3_000_000


def clean(value):
    if isinstance(value,dict):return {key:clean(item) for key,item in value.items()}
    if isinstance(value,list):return [clean(item) for item in value]
    if isinstance(value,str) and value.startswith('/'):
        return '<local>/'+Path(value).name
    return value


def selected(record,keys):
    return {key:record[key] for key in keys if key in record}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('job',type=Path)
    job=parser.parse_args().job.resolve();sources=[];figures=[];methods=[]
    def read(relative,optional=False):
        path=job/relative
        if optional and not path.exists():return None
        raw=path.read_bytes();sources.append(dict(report=relative,size_bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
        return json.loads(raw)
    def write(relative,value):
        path=HERE/relative;path.parent.mkdir(parents=True,exist_ok=True)
        text=json.dumps(clean(value),indent=2,allow_nan=False)+'\n'
        for private in ['/Users/','/private/','/Applications/','/Library/','/var/folders/']:
            if private in text:raise ValueError('Unredacted local path in '+relative)
        path.write_text(text)
    def figure(source,name):
        path=job/source
        if not path.exists():return
        dest=HERE/'figures'/name;shutil.copyfile(path,dest)
        figures.append(dict(file='figures/'+name,source_image=source,size_bytes=dest.stat().st_size,
                            sha256=hashlib.sha256(dest.read_bytes()).hexdigest()))
    context=read('job.json')
    runs={mode:read('validation/'+mode+'_run.json') for mode in ['reference','candidate']}
    cores={mode:read('geometry/'+stem+'.report.json') for mode,stem in [
        ('reference','Pam_TCR_pMHC_contour_zero_reference'),('candidate','Pam_TCR_pMHC_contour_020mm')]}
    audits={'reference':read('validation/reference_contour_audit_corrected.json'),
            'candidate':read('validation/candidate_contour_audit_v3.json')}
    packages={mode:read('validation/package_'+mode+'_normal_lift.json') for mode in runs}
    slices={mode:read('validation/'+mode+'_normal_lift_slice.json') for mode in runs}
    gcode_proofs={mode:read('validation/'+mode+'_normal_lift_gcode_proof.json') for mode in runs}
    fallback=read('validation/fallback_forensics_summary.json')
    unchanged_source=read('validation/source_reference_equivalence_after_audit_fix.json')
    geometry=dict(source=runs['reference']['source'],weave_commit=context['layerloom_commit'],
        audit_commit=context['layerloom_audit_commit'],runs={mode:dict(
            artifact=runs[mode]['artifact'],weave=selected(runs[mode]['weave'],[
                'elapsed_s','peak_process_tree_rss_bytes_observed','rss_poll_interval_s','rss_measurement_complete','exit_code']),
            builder=cores[mode],final_serialized_audit=audits[mode]) for mode in runs},
        fallback_forensics=fallback,unchanged_source_after_auditor_fix=unchanged_source)
    write('evidence/geometry.json',geometry)
    slice_keys=['exit_code','elapsed_s','peak_process_tree_rss_bytes_observed',
        'largest_individual_process_lifetime_max_phys_footprint_bytes','system_swap_peak_used_increase_from_baseline_bytes',
        'rss_poll_interval_s','rss_measurement_complete','phys_footprint_measurement_complete',
        'stop_reason','full_slice_completed','project','gcode','printer_contacted','print_started']
    write('evidence/slicing.json',dict(normal_lift_packages=packages,
        full_slices={mode:selected(slices[mode],slice_keys) for mode in runs},
        final_gcode_proofs=gcode_proofs,
        profile_and_schedule_comparison=read('validation/native_candidate_reference_profile_schedule_comparison.json')))
    official=read('validation/official_235_arc_cache_provenance.json')
    stale=read('validation/stale_arc_payload_estimate.json')
    write('evidence/memory.json',dict(official_release_source=official,source_based_stale_arc_payload_estimate=stale,
        failed_auto_lift_slice=selected(read('validation/candidate_slice.json'),slice_keys),
        note='Source-based payload arithmetic is not an allocator measurement. Full Normal Lift native slices are measured separately in slicing.json; GUI transfer was not benchmarked.'))
    hop=read('validation/auto_vs_normal_lift_control.summary.json')
    for key in ['reference_prefix','candidate_prefix']:
        hop[key].pop('included_layer_z_mm',None)
    repeat=read('validation/reference_normal_lift_repeat_determinism.json')
    repeat.pop('first_differences',None)
    first=read('validation/pam_first_layer_zero_reference.json')
    write('evidence/controls.json',dict(auto_vs_normal_lift_shared_range=hop,
        identical_reference_repeat=repeat,parallel_equivalence=read('validation/parallel_wall_audit_equivalence_proof.json'),
        zero_reference_first_layer=first,zero_reference_first_layer_review=read('validation/pam_first_layer_zero_reference_review.json'),
        candidate_first_layer_hop_control=read('validation/pam_first_layer_normal_lift_review.json'),
        geometry_visual_review=read('validation/pam_geometry_visual_review.json')))
    wall=read('validation/pam_walls.summary.json',optional=True)
    distribution=read('validation/pam_walls.distribution.json',optional=True)
    footprints=read('validation/pam_walls.footprints.json',optional=True)
    interpretation=read('validation/pam_walls.interpretation.json',optional=True)
    visual_review=read('validation/pam_walls.visual_review.json',optional=True)
    complete=bool(wall and distribution and wall.get('parallel_execution',{}).get('all_layers_compared'))
    early=read('validation/pam_walls.partial.footprints.json',optional=True)
    wall_evidence=dict(status='complete' if complete and footprints else 'wall_totals_complete_local_review_pending' if complete else 'pending_full_model_totals',
        candidate_strict_wall_gate_failed_in_observed_layers=True,
        full_wall_summary=wall if complete else None,
        full_distribution=distribution if complete else None,
        complete_wall_interpretation=interpretation,
        final_visual_review=visual_review,
        final_ranked_footprint_diagnostics=footprints,
        earlier_inspected_example_not_a_final_ranking=early,
        recommendation='Use zero intentional XY simplification for this model/profile. Keep the 0.02 mm candidate experimental; passing contours does not certify generated walls.')
    write('evidence/walls.json',wall_evidence)
    retention=read('validation/cleanup_completed.json')
    write('evidence/retention.json',retention)
    rows={}
    for mode in runs:
        rows[mode]=dict(
            xy_tolerance_mm=cores[mode]['xy_tolerance_mm'],triangles=runs[mode]['artifact']['triangles'],
            generic_3mf_bytes=runs[mode]['artifact']['size_bytes'],generic_3mf_sha256=runs[mode]['artifact']['sha256'],
            native_normal_lift_3mf_bytes=packages[mode]['output_size_bytes'],native_normal_lift_3mf_sha256=packages[mode]['output_sha256'],
            weave_elapsed_s=runs[mode]['weave']['elapsed_s'],weave_peak_process_tree_rss_bytes=runs[mode]['weave']['peak_process_tree_rss_bytes_observed'],
            audit_revision=audits[mode]['audit_revision'],audited_contour_planes=audits[mode]['layers'],
            serialized_contours_passed=audits[mode]['passed'],max_contour_distance_mm=audits[mode]['observed_max_boundary_hausdorff_mm'],
            full_slice_completed=slices[mode]['full_slice_completed'],slice_elapsed_s=slices[mode]['elapsed_s'],
            slice_peak_process_tree_rss_bytes=slices[mode]['peak_process_tree_rss_bytes_observed'],
            largest_process_lifetime_physical_footprint_bytes=slices[mode]['largest_individual_process_lifetime_max_phys_footprint_bytes'],
            observed_system_swap_growth_bytes=slices[mode]['system_swap_peak_used_increase_from_baseline_bytes'],
            gcode_bytes=slices[mode]['gcode']['size_bytes'],gcode_sha256=slices[mode]['gcode']['sha256'],
            emitted_print_layers=gcode_proofs[mode]['layer_count'],first_print_z_mm=gcode_proofs[mode]['first_print_z_mm'],
            final_print_z_mm=gcode_proofs[mode]['final_print_z_mm'],estimated_times=gcode_proofs[mode]['estimated_times'])
    runtime=runs['reference']['runtime'];runtime.pop('python_executable',None)
    write('metrics.json',dict(case='Pam TCR-pMHC reinforced ribbon/stick model',
        status=wall_evidence['status'],weave_commit=context['layerloom_commit'],audit_commit=context['layerloom_audit_commit'],
        source=selected(runs['reference']['source'],['sha256','size_bytes','vertices','triangles']),
        oriented_source_dimensions_mm=context['source_dimensions_mm'],source_preparation='Previously approved/pre-simplified labeled source; no new rotation or scaling in this run.',
        layer_schedule=context['schedule'],physical_filament_slots=context['filament_slots'],
        peptide_patterns=context['peptide_patterns'],ribbon_patterns=context['ribbon_patterns'],
        previous_woven_baseline=selected(context['baseline_final'],['sha256','size_bytes','triangles']),
        runtime=runtime,rows=rows,candidate_exact_fallback_planes=cores['candidate']['exact_fallback_layers'],
        wall_gate='failed; full totals pending' if not complete else 'passed' if wall['wall_paths_validated'] else 'failed',
        recommendation='zero intentional XY simplification / Normal Lift',
        wall_summary=selected(wall,['layers','failed_layers','combined_feature_geometry_failed_layers','certified_over_tolerance_layers','max_displacement_upper_mm','max_combined_feature_displacement_upper_mm']) if complete else None,
        weighted_wall_distance=distribution['weighted']['both'] if complete else None,
        physical_print_tested=False,printer_contacted=False,gui_transfer_tested=False,
        notes=['MB/GB in README use decimal units; raw bytes are authoritative.',
            'Weaves ran concurrently; elapsed times are contextual, not isolated throughput measurements.',
            'Zero disables discretionary XY simplification but retains documented numerical regularization.',
            'No large prepared-model, 3MF or G-code binaries are distributed in this evidence bundle.']))
    figure('validation/pam_geometry-layers.png','contours.png')
    figure('validation/pam_geometry-silhouettes.png','silhouettes.png')
    figure('validation/pam_first_layer_zero_reference.png','zero-first-layer.png')
    if footprints and footprints.get('examples'):
        example=max(footprints['examples'],key=lambda x:x['sampled_centerline_error_mm'])
        figure(example['image'],'worst-wall.png')
    for name in ['compare_model_walls.py','parallel_wall_audit.py','first_layer_bed.py']:
        dest=HERE/'methods'/name;shutil.copyfile(job/'scripts'/name,dest)
        methods.append(dict(file='methods/'+name,sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),size_bytes=dest.stat().st_size))
    manifest=dict(source_reports=sources,figures=figures,method_snapshots=methods,
        omitted='Large model/G-code files, logs, full layer JSONL and local absolute paths.',
        artifact_provenance='Hashes inside reports identify retained local artifacts. Source-report hashes identify the original unsanitized JSON, while public JSON uses artifact basenames instead of absolute local paths.')
    write('manifest.json',manifest)
    total=sum(path.stat().st_size for path in HERE.rglob('*') if path.is_file())
    if total>=LIMIT_BYTES:raise ValueError(f'Bundle exceeds 3 MB budget: {total} bytes')
    print(json.dumps(dict(status=wall_evidence['status'],bundle_bytes=total,figures=len(figures),source_reports=len(sources)),indent=2))


if __name__=='__main__':main()
