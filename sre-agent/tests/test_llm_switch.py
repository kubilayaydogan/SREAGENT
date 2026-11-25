"""Model çalışırken değiştirilebilir: genel ve RCA istemcisi aynı seçimi izler, istemciler önbellekte kalır."""
from airlab_agent.config import Settings, load_yaml_config
from airlab_agent.llm import Llm


def _llm():
    return Llm(Settings(anthropic_key="sk-test", claude_model="claude-opus-5-5", claude_rca_model="claude-opus-5-5",
                        cfg=load_yaml_config(None)))


def test_switch_changes_both_clients_and_caches():
    llm = _llm()
    assert llm.model_name == llm.rca_model_name == "claude-opus-5-5"
    opus_chat, opus_rca = llm.chat, llm.rca_chat
    assert opus_chat.model == "claude-opus-5-5" and opus_chat is llm.chat           # önbellek
    assert opus_rca.max_tokens == 16000 and opus_rca is not opus_chat
    llm.set_model("claude-sonnet-5-5")
    assert llm.model_name == llm.rca_model_name == "claude-sonnet-5-5"
    assert llm.chat.model == "claude-sonnet-5-5" and llm.rca_chat.model == "claude-sonnet-5-5"
    llm.set_model(None)                                                              # override kalkar → env değeri
    assert llm.chat is opus_chat and llm.rca_chat is opus_rca


def test_profiles_apply_per_task_and_rebuild_client_on_change():
    llm = _llm()
    env_chat = llm.chat
    llm.set_profiles({"rca": {"model": "claude-sonnet-5-5", "max_tokens": 8000, "timeout_s": 90, "effort": "high",
                              "api_key": "sk-other"}})
    assert llm.model_name == "claude-opus-5-5" and llm.rca_model_name == "claude-sonnet-5-5"
    assert llm.chat is env_chat                                                      # chat profili yok → env istemcisi
    rca = llm.rca_chat
    assert rca.model == "claude-sonnet-5-5" and rca.max_tokens == 8000 and rca.default_request_timeout == 90
    llm.set_profiles({"rca": {"model": "claude-sonnet-5-5", "max_tokens": 4000}})     # değer değişti → yeni istemci
    assert llm.rca_chat is not rca and llm.rca_chat.max_tokens == 4000
    llm.set_model("claude-opus-5-5")                                                 # arayüz seçimi profil modelini ezer
    assert llm.rca_model_name == "claude-opus-5-5"
    llm.set_profiles(None)
    llm.set_model(None)
    assert llm.chat is env_chat
