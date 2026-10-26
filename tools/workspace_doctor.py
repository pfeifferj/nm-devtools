"""Read explicit workspace configuration; execute only fixed local Git reads."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def absolute(value, label):
    require(isinstance(value, str) and Path(value).is_absolute(), f"{label} must be an absolute path")
    return Path(value).resolve()


def relative(value):
    return isinstance(value, str) and value and not Path(value).is_absolute() and ".." not in Path(value).parts and "\x00" not in value


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON field: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def load_config(path):
    data = read_json(path)
    require(isinstance(data, dict) and {"schema_version", "checkouts"} <= data.keys() <= {
        "schema_version", "checkouts", "capabilities", "merged_specs", "specdoc", "task_manifest",
    }, "invalid doctor config fields")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1, "unsupported doctor config schema")
    checkouts = data["checkouts"]
    require(isinstance(checkouts, dict) and checkouts, "configure at least one explicit checkout")
    for name, value in checkouts.items():
        require(isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_-]*", name), "invalid checkout name")
        require(isinstance(value, dict) and set(value) == {"path", "required"} and type(value["required"]) is bool, f"invalid checkout: {name}")
        absolute(value["path"], f"checkout {name}")
    capabilities = data.get("capabilities", [])
    require(isinstance(capabilities, list), "capabilities must be a list")
    ids = set()
    for value in capabilities:
        require(isinstance(value, dict) and {"id", "required", "checkout"} <= value.keys() <= {
            "id", "required", "checkout", "files", "commands", "guidance",
        }, "invalid capability fields")
        require(isinstance(value["id"], str) and value["id"] and value["id"] not in ids, "invalid or duplicate capability ID")
        ids.add(value["id"])
        require(type(value["required"]) is bool and isinstance(value["checkout"], str) and value["checkout"] in checkouts, "invalid capability checkout/required")
        require(value.get("files") or value.get("commands"), "capability needs files or commands")
        for field in ("files", "commands"):
            items = value.get(field, [])
            require(isinstance(items, list) and all(isinstance(v, str) and v for v in items), f"invalid capability {field}")
        require(all(relative(v) for v in value.get("files", [])), "capability files must be checkout-relative")
        require(all(re.fullmatch(r"[A-Za-z0-9_.+-]+", v) for v in value.get("commands", [])), "commands must be executable names, never shell commands")
        require("guidance" not in value or isinstance(value["guidance"], str), "invalid guidance")
    merged = data.get("merged_specs")
    if merged is not None:
        require(isinstance(merged, dict) and {"checkout", "ref", "paths"} <= merged.keys() <= {"checkout", "ref", "paths", "expected_commit"}, "invalid merged_specs fields")
        require(isinstance(merged["checkout"], str) and merged["checkout"] in checkouts and isinstance(merged["ref"], str) and merged["ref"] and "\x00" not in merged["ref"], "invalid merged spec checkout/ref")
        require(isinstance(merged["paths"], list) and merged["paths"] and all(relative(v) for v in merged["paths"]), "merged spec paths must be checkout-relative")
        require("expected_commit" not in merged or isinstance(merged["expected_commit"], str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", merged["expected_commit"]), "expected_commit must be a full Git object ID")
    specdoc = data.get("specdoc")
    if specdoc is not None:
        require(isinstance(specdoc, dict) and set(specdoc) == {"checkout", "require_mcp"} and isinstance(specdoc["checkout"], str) and specdoc["checkout"] in checkouts and type(specdoc["require_mcp"]) is bool, "invalid specdoc fields")
    if "task_manifest" in data:
        absolute(data["task_manifest"], "task_manifest")
    return data


def git_read(git, root, *arguments):
    if not git:
        return None
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
           "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    # Repository fsmonitor configuration can otherwise execute a project command.
    try:
        result = subprocess.run([git, "--no-pager", "--no-optional-locks", "--no-lazy-fetch", "-c", "core.fsmonitor=false",
                                 "-c", "core.untrackedCache=false", "-C", str(root), *arguments],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=10, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def file_identity(path):
    try:
        content = path.read_bytes()
        return {"path": str(path.resolve()), "sha256": digest(content), "status": "found"}
    except (FileNotFoundError, IsADirectoryError):
        return {"path": str(path), "status": "missing"}


def checkout_report(name, value, git):
    root = absolute(value["path"], name)
    record = {"id": name, "path": str(root), "required": value["required"], "status": "missing", "head": None, "branch": None,
              "dirty": None, "tracked_changes": [], "untracked": []}
    top = git_read(git, root, "rev-parse", "--show-toplevel")
    if top is None:
        record["guidance"] = "Install Git or select an existing Git checkout explicitly; nothing was cloned or selected automatically."
        return record
    if Path(os.fsdecode(top).strip()).resolve() != root:
        record["guidance"] = "Select the Git checkout root, rather than a directory inside another checkout."
        return record
    head = git_read(git, root, "rev-parse", "--verify", "HEAD")
    branch = git_read(git, root, "symbolic-ref", "--quiet", "--short", "HEAD")
    status = git_read(git, root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    record.update(status="found" if head is not None and status is not None else "unknown",
                  head=head.decode().strip() if head is not None else None,
                  branch=branch.decode().strip() if branch is not None else None)
    if status is not None:
        fields = status.split(b"\0")
        index = 0
        while index < len(fields):
            item = fields[index]
            index += 1
            if not item:
                continue
            code, filename = os.fsdecode(item[:2]), os.fsdecode(item[3:])
            if code == "??":
                record["untracked"].append(filename)
            else:
                change = {"status": code, "path": filename}
                if "R" in code or "C" in code:
                    change["original_path"] = os.fsdecode(fields[index])
                    index += 1
                record["tracked_changes"].append(change)
        record["dirty"] = bool(record["tracked_changes"] or record["untracked"])
    record["guidance"] = "Inspect the reported edits before reusing this checkout; no files were changed." if record["dirty"] else "Detached HEAD is explicit when branch is null; no checkout was switched."
    return record


def installed_package(start, name):
    for root in (start, *start.parents):
        candidate = root / "node_modules" / name / "package.json"
        if candidate.is_file():
            return candidate
    return None


def specdoc_report(config, roots):
    root = roots[config["checkout"]]
    cli = root / "cli"
    mcp = root / "mcp"
    engine = cli / "lib/context/runtime.js"
    installed_engine = installed_package(mcp, "specdoc-cli")
    mcp_engine = installed_engine.parent / "lib/context/runtime.js" if installed_engine else None
    package = cli / "package.json"
    metadata = read_json(package) if package.is_file() else {}
    require(isinstance(metadata, dict) and isinstance(metadata.get("optionalDependencies", {}), dict) and isinstance(metadata.get("engines", {}), dict), "invalid SpecDoc package metadata")
    parser = []
    for name in ("tree-sitter", "tree-sitter-rust"):
        path = installed_package(cli, name)
        value = read_json(path) if path else {}
        require(isinstance(value, dict), "invalid installed parser package metadata")
        parser.append({"name": name, "declared": metadata.get("optionalDependencies", {}).get(name),
                       "version": value.get("version"), "path": str(path.resolve()) if path else None,
                       "status": "found" if path else "missing", "required_for": "local Rust context indexing", "runtime_support": "unknown"})
    cli_identity = file_identity(engine)
    mcp_identity = file_identity(mcp_engine) if mcp_engine else {"path": None, "status": "missing"}
    return {"checkout": str(root), "package": {"name": metadata.get("name"), "version": metadata.get("version"),
            "node_required": metadata.get("engines", {}).get("node")},
            "cli": {"entrypoint": file_identity(cli / "bin/specdoc.js"), "engine": cli_identity},
            "mcp": {"required": config["require_mcp"], "entrypoint": file_identity(mcp / "server.js"), "engine": mcp_identity},
            "same_engine_bytes": cli_identity.get("sha256") == mcp_identity.get("sha256") if cli_identity.get("sha256") and mcp_identity.get("sha256") else None,
            "parsers": parser, "runtime_support": "unknown",
            "guidance": "Package metadata and engine bytes were inspected without loading JavaScript or native modules. Node version, native ABI, MCP transport and context operation support remain unknown; use the selected checkout's documented checks."}


def merged_report(config, roots, git):
    root = roots[config["checkout"]]
    commit = git_read(git, root, "rev-parse", "--verify", "--end-of-options", config["ref"] + "^{commit}")
    revision = commit.decode().strip() if commit else None
    matches = revision == config["expected_commit"] if "expected_commit" in config and revision else None
    record = {"checkout": str(root), "ref": config["ref"], "revision": revision, "expected_commit_matches": matches,
              "authority": "caller-selected-merged", "status": "found" if revision and matches is not False else "missing", "sources": [],
              "guidance": "The configured ref is a caller assertion of merged authority. Local Git verifies bytes, not forge merge status or remote freshness; no fetch was performed."}
    for path in config["paths"]:
        blob = git_read(git, root, "rev-parse", "--verify", revision + ":" + path) if revision else None
        kind = git_read(git, root, "cat-file", "-t", blob.decode().strip()) if blob else None
        content = git_read(git, root, "cat-file", "blob", blob.decode().strip()) if kind == b"blob\n" else None
        record["sources"].append({"path": path, "revision": revision, "blob": blob.decode().strip() if blob else None,
                                  "sha256": digest(content) if content is not None else None,
                                  "status": "found" if content is not None else "missing"})
    if any(s["status"] == "missing" for s in record["sources"]):
        record["status"] = "missing"
    return record


def manifest_report(path):
    path = absolute(path, "task_manifest")
    record = file_identity(path)
    record["content_verified"] = False
    if record["status"] == "missing":
        record["guidance"] = "Pin the task manifest explicitly with the selected SpecDoc checkout."
        return record
    value = read_json(path)
    require(isinstance(value, dict) and value.get("schemaVersion") == 1 and value.get("kind") == "specdoc-task-context", "unsupported task manifest")
    encoded = json.dumps({k: v for k, v in value.items() if k != "id"}, ensure_ascii=False, separators=(",", ":"))
    record["identity_matches"] = value.get("id") == digest(encoded.encode("utf-8"))
    record.update(id=value.get("id"), implementation=value.get("implementation"), selection=value.get("selection"),
                  sources=value.get("sources", []), required_gaps=value.get("requiredGaps", []))
    record["status"] = "unknown" if record["identity_matches"] else "missing"
    record["guidance"] = "File identity only: SpecDoc must verify manifest identity, Git bytes, dependencies and completeness before acceptance."
    return record


def inspect(config_path, tool_root):
    require(config_path.is_absolute(), "doctor --config must be an absolute path")
    config_path = config_path.resolve()
    data = load_config(config_path)
    roots = {name: absolute(value["path"], name) for name, value in data["checkouts"].items()}
    # Ignore relative PATH entries so an unrelated current directory cannot select Git.
    search = os.pathsep.join(p for p in os.get_exec_path() if Path(p).is_absolute())
    git = shutil.which("git", path=search)
    checkouts = [checkout_report(name, value, git) for name, value in data["checkouts"].items()]
    capabilities = []
    for capability in data.get("capabilities", []):
        root = roots[capability["checkout"]]
        checks = [{"kind": "file", "name": name, **file_identity(root / name)} for name in capability.get("files", [])]
        checks.extend({"kind": "command", "name": name, "path": shutil.which(name, path=search),
                       "status": "found" if shutil.which(name, path=search) else "missing", "version": "unknown"}
                      for name in capability.get("commands", []))
        capabilities.append({**capability, "checks": checks, "status": "missing" if any(c["status"] == "missing" for c in checks) else "unknown",
                             "guidance": capability.get("guidance") or "Select a checkout containing the declared files and provision missing executables separately; runtime readiness has not been established."})
    result = {"schema_version": 1, "kind": "workspace-doctor", "config": file_identity(config_path),
              "tool_catalog": file_identity(tool_root / "tools/oracles.json"), "git": {"path": git, "status": "found" if git else "missing"},
              "checkouts": checkouts, "capabilities": capabilities,
              "limits": ["Read-only fixed Git queries and filesystem inspection only; no project commands, installs, network, VM access or services.",
                         "Presence does not establish executable versions, native parser support, merged authority, runtime readiness or test success."]}
    if "merged_specs" in data:
        result["merged_specs"] = merged_report(data["merged_specs"], roots, git)
    if "specdoc" in data:
        result["specdoc"] = specdoc_report(data["specdoc"], roots)
    if "task_manifest" in data:
        result["task_manifest"] = manifest_report(data["task_manifest"])
    missing = ["checkout:" + c["id"] for c in checkouts if c["required"] and c["status"] != "found"]
    missing.extend("capability:" + c["id"] for c in capabilities if c["required"] and c["status"] == "missing")
    if result.get("merged_specs", {}).get("status") == "missing":
        missing.append("merged_specs")
    if "specdoc" in result:
        specdoc = result["specdoc"]
        if specdoc["cli"]["entrypoint"]["status"] == "missing" or specdoc["cli"]["engine"]["status"] == "missing":
            missing.append("specdoc:cli")
        if data["specdoc"]["require_mcp"] and (specdoc["mcp"]["entrypoint"]["status"] == "missing" or specdoc["mcp"]["engine"]["status"] == "missing"):
            missing.append("specdoc:mcp")
        if data["specdoc"]["require_mcp"] and specdoc["same_engine_bytes"] is False:
            missing.append("specdoc:shared-engine")
        missing.extend("specdoc:" + p["name"] for p in specdoc["parsers"] if p["status"] == "missing")
        specdoc["node"] = {"path": shutil.which("node", path=search), "version": "unknown"}
        if not specdoc["node"]["path"]:
            missing.append("specdoc:node")
    if result.get("task_manifest", {}).get("status") == "missing":
        missing.append("task_manifest")
    result["missing_required"] = missing
    result["status"] = "missing" if missing else "unknown"
    return result


def main(config, as_json, tool_root):
    result = inspect(config, tool_root)
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Workspace prerequisites: {result['status']}; runtime readiness remains unknown")
        for checkout in result["checkouts"]:
            print(f"  {checkout['id']}: {checkout['path']} {checkout['head'] or 'no HEAD'} branch={checkout['branch']} dirty={checkout['dirty']}")
            print(f"    tracked changes: {len(checkout['tracked_changes'])}; untracked: {len(checkout['untracked'])}; {checkout['status']}")
        for capability in result["capabilities"]:
            print(f"  {capability['id']}: {capability['status']} ({'required' if capability['required'] else 'optional'})")
        for name in ("merged_specs", "specdoc", "task_manifest"):
            if name in result:
                print(f"  {name}: {json.dumps(result[name])}")
        for item in result["missing_required"]:
            print(f"  missing required: {item}")
        print("Use --json for source hashes, individual checks and guidance. No project commands ran.")
    return 1 if result["missing_required"] else 0
