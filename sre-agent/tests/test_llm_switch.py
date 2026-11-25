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
