"""Read-only Model Context Protocol (MCP) JSON-RPC 2.0 tool surface."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from statebisect.dashboard.serializers import safe_redact, serialize_candidate, serialize_verification_run
from statebisect.reporting import generate_text_report
from statebisect.storage import Storage

READ_ONLY_MCP_TOOLS = [
    {
        "name": "get_investigation_summary",
        "description": "Retrieve high-level summary, status, and baseline metadata for an investigation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "investigation_id": {
                    "type": "string",
                    "description": "Identifier of the target investigation.",
                }
            },
            "required": ["investigation_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_evidence_ids",
        "description": "List all stable evidence IDs (candidates, trials, verifications, gaps) for an investigation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "investigation_id": {
                    "type": "string",
                    "description": "Identifier of the target investigation.",
                }
            },
            "required": ["investigation_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_saved_report",
        "description": "Retrieve the formatted diagnostic text report for an investigation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "investigation_id": {
                    "type": "string",
                    "description": "Identifier of the target investigation.",
                }
            },
            "required": ["investigation_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_candidate_or_verification",
        "description": "Retrieve specific candidate change or verification run details by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "investigation_id": {
                    "type": "string",
                    "description": "Identifier of the target investigation.",
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["candidate", "verification"],
                    "description": "Type of entity to retrieve.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Identifier of the specific candidate or verification run.",
                },
            },
            "required": ["investigation_id", "entity_type", "entity_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
]


class ReadOnlyMcpServer:
    """Read-only MCP JSON-RPC 2.0 tool handler strictly scoped to an investigation.

    Implements tools/list and tools/call conforming to the official Model Context Protocol specification.
    """

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def handle_json_rpc(self, request: dict[str, Any]) -> dict[str, Any]:
        """Process incoming MCP JSON-RPC 2.0 request."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if request.get("jsonrpc") != "2.0":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32600, "message": "Invalid Request: jsonrpc must be '2.0'"},
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": READ_ONLY_MCP_TOOLS},
            }

        if method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})
            return self._call_tool(req_id, tool_name, args)

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: '{method}'"},
        }

    def _call_tool(self, req_id: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a narrow, read-only tool safely."""
        known_tools = {t["name"] for t in READ_ONLY_MCP_TOOLS}
        if tool_name not in known_tools:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not implemented or not permitted."},
            }

        inv_id = args.get("investigation_id", "").strip()
        if not inv_id:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"isError": True, "content": [{"type": "text", "text": "Missing required 'investigation_id' argument."}]},
            }

        inv = self.storage.get_investigation(inv_id)
        if not inv:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"isError": True, "content": [{"type": "text", "text": f"Investigation '{inv_id}' not found."}]},
            }

        try:
            if tool_name == "get_investigation_summary":
                symptom_redacted = safe_redact(inv.symptom)
                data = {
                    "investigation_id": inv.id,
                    "target_system": inv.target_system,
                    "symptom": symptom_redacted,
                    "status": inv.status.value if hasattr(inv.status, "value") else str(inv.status),
                    "baseline_id": inv.baseline_id,
                    "created_at": inv.created_at,
                }
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(data, indent=2)}], "isError": False},
                }

            if tool_name == "list_evidence_ids":
                ids = [f"ev-inv-{inv.id}"]
                for c in self.storage.get_candidates(inv_id):
                    ids.append(f"ev-cand-{c.candidate_id}")
                for vr in self.storage.list_verification_runs(investigation_id=inv_id):
                    ids.append(f"ev-verif-{vr.verification_id}")
                for t in self.storage.get_isolated_trials(investigation_id=inv_id)[:20]:
                    ids.append(f"ev-trial-{t.trial_id}")
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": json.dumps({"evidence_ids": ids}, indent=2)}], "isError": False},
                }

            if tool_name == "get_saved_report":
                vrs = self.storage.list_verification_runs(investigation_id=inv_id)
                if not vrs:
                    text_report = f"No verification runs yet recorded for investigation '{inv_id}'."
                else:
                    from statebisect.verification.reporting import format_verification_text
                    text_report = format_verification_text(vrs[0])
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": text_report}], "isError": False},
                }

            if tool_name == "get_candidate_or_verification":
                entity_type = args.get("entity_type")
                entity_id = args.get("entity_id", "").strip()
                if entity_type == "candidate":
                    cand = self.storage.get_candidate(entity_id)
                    if not cand or getattr(cand, "investigation_id", "") != inv_id:
                        return {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {"isError": True, "content": [{"type": "text", "text": f"Candidate '{entity_id}' not found in investigation."}]},
                        }
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"content": [{"type": "text", "text": json.dumps(serialize_candidate(cand), indent=2)}], "isError": False},
                    }
                elif entity_type == "verification":
                    v_runs = [vr for vr in self.storage.list_verification_runs(investigation_id=inv_id) if vr.verification_id == entity_id]
                    if not v_runs:
                        return {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {"isError": True, "content": [{"type": "text", "text": f"Verification run '{entity_id}' not found in investigation."}]},
                        }
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"content": [{"type": "text", "text": json.dumps(serialize_verification_run(v_runs[0]), indent=2)}], "isError": False},
                    }
                else:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"isError": True, "content": [{"type": "text", "text": f"Invalid entity_type '{entity_type}'. Must be 'candidate' or 'verification'."}]},
                    }

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not implemented."},
            }

        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"isError": True, "content": [{"type": "text", "text": f"Tool error: {str(e)}"}]},
            }
