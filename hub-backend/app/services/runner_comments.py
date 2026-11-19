"""Runner follow-up admission, attachments and explicit executor selection."""
import os
import uuid

from . import ai_local, runner_admission, runner_local_rpc


def _transfers(message):
    """Return packet transfer IDs without treating local file paths as transfers."""
    out = set()
    if isinstance(message, dict) and "transfer" in message:
        packets = (message,)
    else:
        packets = ((message or {}).get("files"), (message or {}).get("skills"))
    for packet in packets:
        if isinstance(packet, dict):
            transfer = str(packet.get("transfer") or "")
            if transfer:
                out.add(transfer)
    return out


def _previous_transfers(machine, turn_id):
    admission = getattr(machine, "_admission", None)
    item = admission.get("comment:" + str(turn_id or "")) if admission is not None else None
    return _transfers(item.payload if item is not None else {})


async def _drop_transfers(message, drop_transfer, *, keep=()):
    """Drop this request's temporary packets, preserving an accepted retry source."""
    if drop_transfer is None:
        return
    preserved = {str(value) for value in keep if str(value)}
    for transfer in sorted(_transfers(message) - preserved):
        try:
            await drop_transfer(transfer)
        except Exception:  # noqa: BLE001 - cleanup must not mask the admission result
            pass


def _duplicate(machine, ref, turn_id, message):
    previous = machine._admission.get("comment:" + turn_id)
    if previous is None:
        return None
    expected = previous.request_digest or (runner_admission.comment_digest(previous.ref, previous.payload)
                                           if previous.payload is not None else "")
    if previous.ref != ref or (expected and expected != runner_admission.comment_digest(ref, message)):
        return {"ok": False, "code": "MESSAGE_ID_CONFLICT", "error": "消息编号已用于其他内容或执行选择"}
    return {"ok": True, "data": machine._admission.receipt(previous, duplicate=True)}


async def comment(machine, m, read_local_files, read_packet=None, drop_transfer=None):
    from app.services import local_stream
    from app.services import dispatch_identity
    ref = str(m.get("ref") or "")
    turn_id = dispatch_identity.clean_dispatch_id(m.get("turnId"))
    if not ref:
        await _drop_transfers(m, drop_transfer, keep=_previous_transfers(machine, turn_id))
        return {"ok": False, "error": "会话标识不能为空"}
    if not turn_id:
        turn_id = "turn:" + uuid.uuid4().hex
        m = dict(m)
        m["turnId"] = turn_id
    selected = None
    if m.get("executionSelection") is not None:
        from . import execution_selection
        try:
            m = {**m, "executionSelection": execution_selection.normalize(m["executionSelection"])}
        except execution_selection.SelectionError as exc:
            await _drop_transfers(
                m, drop_transfer,
                keep=_previous_transfers(machine, turn_id) if not m.get("_admitted") else ())
            return {"ok": False, "code": getattr(exc, "code", "EXECUTION_SELECTION_INVALID"), "error": str(exc)}
    if not m.get("_admitted"):
        previous = _duplicate(machine, ref, turn_id, m)
        if previous is not None:
            await _drop_transfers(m, drop_transfer, keep=_previous_transfers(machine, turn_id))
            return previous
    if m.get("executionSelection") is not None:
        from . import execution_selection, runner_turn_selection
        try:
            raw = execution_selection.normalize(m["executionSelection"])
            selected = await runner_local_rpc.to_thread(runner_turn_selection.validate, ref, raw, m.get("cfg"))
            from . import local_base
            with local_base._LOCK:
                switching = selected != execution_selection.current(local_base._RUNS.get(ref) or {})
            machine._reap_admission()
            if switching and not m.get("_admitted") and machine._admission.latest_for_ref(ref):
                raise execution_selection.SelectionError("请等待当前轮次及排队消息完成后再切换", "EXECUTION_SWITCH_BUSY")
            m = {**m, "executionSelection": raw, "_acceptedSelection": selected}
        except (execution_selection.SelectionError, ValueError) as exc:
            await _drop_transfers(
                m, drop_transfer,
                keep=_previous_transfers(machine, turn_id) if not m.get("_admitted") else ())
            return {"ok": False, "code": getattr(exc, "code", "EXECUTION_SELECTION_INVALID"), "error": str(exc)}
    if not m.get("_admitted"):
        # Selection validation awaits native state. Another request may have
        # admitted this business ID during that await, including different text.
        previous = _duplicate(machine, ref, turn_id, m)
        if previous is not None:
            await _drop_transfers(m, drop_transfer, keep=_previous_transfers(machine, turn_id))
            return previous
        result = await machine._admit(m, key="comment:" + turn_id, ref=ref,
            category=runner_admission.INTERACTIVE, operation="comment")
        if selected and result.get("ok"):
            item = machine._admission.get("comment:" + turn_id)
            result["data"] = {**result.get("data", {}), "acceptedSelection": selected,
                              "acceptedSelectionRevision": item.sequence}
        return result
    user_text = str(m.get("text") or "")
    text = str(m.get("promptText") or user_text)
    prepared_plugins = None
    if m.get("pluginTurn"):
        from . import runner_turn_plugins
        try:
            prepared_plugins = await runner_turn_plugins.prepare(machine, m)
            text = prepared_plugins["promptText"]
            m = {**m, "plugins": prepared_plugins["plugins"]}
        except (ValueError, KeyError, TimeoutError) as exc:
            await _drop_transfers(m, drop_transfer)
            return {"ok": False, "error": str(exc)}
    prepared_skills = None
    if m.get("turnSkills"):
        from . import conversation_turn_skills
        try:
            prepared_skills = await conversation_turn_skills.prepare(machine, m)
            text += prepared_skills["prompt"]
        except (ValueError, KeyError, OSError) as exc:
            await _drop_transfers(m, drop_transfer)
            return {"ok": False, "error": str(exc)}
    staged = []
    packet = m.get("files") or {}
    if packet.get("items"):
        if read_packet is None:
            await _drop_transfers(m, drop_transfer)
            return {"ok": False, "error": "执行机不支持本轮材料"}
        try:
            files = await runner_local_rpc.to_thread(read_packet, packet, "本轮补充文件")
            inputs = [(meta["filename"], data, meta.get("contentType") or "application/octet-stream") for meta, data in files]
            staged = await runner_local_rpc.to_thread(ai_local._stage_inputs, ref, inputs)
            if len(staged) != len(inputs):
                return {"ok": False, "error": "本轮文件未完整保存，没有继续执行"}
            text += "\n\n【本轮补充文件】\n" + "\n".join("- %s：%s" % (item["name"], item["path"]) for item in staged)
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            await _drop_transfers(packet, drop_transfer)
    elif packet.get("transfer"):
        await _drop_transfers(packet, drop_transfer)
    if m.get("_localParent") and m.get("localFiles"):
        files = await runner_local_rpc.to_thread(
            read_local_files, m.get("localFiles") or [], "\u8865\u5145\u9644\u4ef6")
        inputs = [(str(meta.get("filename") or "\u9644\u4ef6"), data,
                   str(meta.get("contentType") or "application/octet-stream"))
                  for meta, data in files]
        staged = await runner_local_rpc.to_thread(ai_local._stage_inputs, ref, inputs)
        if staged:
            text += "\n\n\u3010\u672c\u8f6e\u8865\u5145\u6587\u4ef6\u3011\n" + "\n".join(
                "- %s\uff1a%s" % (item.get("name") or "\u9644\u4ef6", item.get("path") or "")
                for item in staged)
    if m.get("recordUser"):
        await runner_local_rpc.to_thread(local_stream.record_user_sync, ref, user_text, staged,
                                m.get("plugins"), m.get("sentAt"))
    from app.services import runtime_images
    try:
        cfg = {**dict(m.get("cfg") or {}), "_admissionKey": str(m.get("_admissionKey") or "")}
        # Controls belong to the admitted message, never a renewable model grant.
        for key in ("taskGoal", "taskGoalControl"):
            cfg.pop(key, None)
            if key in m:
                cfg[key] = m[key]
        if prepared_skills is not None:
            cfg["skillSelection"] = prepared_skills["selection"]
        if prepared_plugins is not None:
            cfg["pluginResourceBundles"] = prepared_plugins["resourceBundles"]
        elif not m.get("_localParent"):
            cfg["pluginResourceBundles"] = []
        if selected is not None:
            cfg["_executionSelection"] = selected
        turn_cfg = await runner_local_rpc.to_thread(runtime_images.prepare, cfg,
            staged, os.path.join(ai_local._store_dir(ref), "inputs"))
    except (runtime_images.ImageInputError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    result = await runner_local_rpc.to_thread(
        local_stream.comment_sync, turn_cfg, ref, text)
    return {"ok": bool(result.get("ok")), "data": result,
            "error": str(result.get("error") or "")}
