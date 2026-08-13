import importlib.util, pathlib, sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
DOMAIN=ROOT/'services/api/app/domain'

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

patches=load('v8_patches',DOMAIN/'patches.py')
quality=load('v8_quality',DOMAIN/'quality.py')

def sample():
    return {"title":"灯还亮","language":"zh-CN","theme":"城市夜归","mood":["克制","温暖"],"genre":"piano pop ballad","bpm":72,"vocal":{"type":"female","delivery":"breathy intimate"},"hook":"灯还亮","styles":"Mandarin piano pop ballad, intimate vocal, felt piano, warm strings, controlled dynamics, chorus lift, close vocal, plate reverb, NO aggressive drums","lyrics":"[Verse]\n雨落在玻璃上\n我推开旧车门\n[Chorus]\n灯还亮 灯还亮\n[Bridge]\n原来我把告别戴在身上\n[pause 1.0s]\n终于放下\n[Final Chorus]\n灯还亮 灯还亮\n我继续走向光\n[Outro]\n[silence 3.0s]","structure":{"sections":["Verse","Chorus","Bridge","Final Chorus","Outro"]}}

def test_locked_patch_is_rejected():
    try:patches.apply_patch(sample(),[{"op":"replace","path":"/lyrics","value":"x"}],["/lyrics"])
    except patches.PatchError:pass
    else:raise AssertionError('locked patch was applied')

def test_quality_is_deterministic_and_has_28_dimensions():
    a=quality.evaluate(sample());b=quality.evaluate(sample());assert a==b;assert len(a['dimensions'])==28;assert a['engine_version'].startswith('v12-')

def test_unlocked_patch_preserves_other_fields():
    doc=sample();out=patches.apply_patch(doc,[{"op":"replace","path":"/bpm","value":82}],["/lyrics"]);assert out['bpm']==82 and out['lyrics']==doc['lyrics']

def test_runtime_song_spec_schema_accepts_sample_and_rejects_invalid_bpm():
    import json
    from jsonschema import Draft202012Validator
    schema=json.loads((ROOT/'shared/contracts/song-spec-runtime-v1.schema.json').read_text())
    validator=Draft202012Validator(schema)
    assert not list(validator.iter_errors(sample()))
    bad=sample();bad['bpm']=500
    assert list(validator.iter_errors(bad))

def test_quality_implements_frozen_v21_tee_contract():
    result=quality.evaluate(sample())
    v=result['variables']
    assert result['lyrics_full_score']==465
    assert 0 <= v['PAC_n'] <= 1
    assert -1 <= v['TSMI_n'] <= 1
    assert 0 <= v['NSRQ_n'] <= 1
    assert 0 <= v['C3AC_n'] <= 1
    assert abs(v['SoftPenalty']-35*(1-v['G_struct']))<0.01
    expected=(20*quality._clamp(-v['TSMI_n']/0.15)
              +10*quality._clamp((0.20-v['PAC_n'])/0.20)
              +10*quality._clamp((0.25-v['C3AC_n'])/0.25))
    assert abs(v['CriticalPenalty']-expected)<0.01
    assert abs(v['TEE_offset']-(v['TEE_pct_final']-result['lyrics_score']))<0.01


def test_cantonese_profile_uses_470_point_lyrics_scale():
    spec=sample();spec['language']='yue';spec['lyrics']='[Verse]\n我唔记得嗰盏灯\n[Chorus]\n灯还亮 灯还亮\n[Bridge]\n原来我揽实旧影\n[pause 1.5s]\n[Final Chorus]\n我知 灯还亮\n由佢挂在胸口\n[Outro]\n[silence 5.0s]'
    result=quality.evaluate(spec)
    assert result['language_profile']=='cantonese'
    assert result['lyrics_full_score']==470
