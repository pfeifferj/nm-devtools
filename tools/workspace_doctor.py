"""Read explicit workspace configuration; execute only fixed local Git reads."""

import functools
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


OBJECT_ID = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
ORACLE_ID = r"[a-z][a-z0-9_.-]*"
# --no-lazy-fetch first appeared in Git 2.45.
MINIMUM_GIT = (2, 45)
MAX_FILE_BYTES = 16 * 1024 * 1024


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


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON field: {key}")
        result[key] = value
    return result


def read_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        require(stat.S_ISREG(metadata.st_mode), f"not a regular file: {path}")
        require(metadata.st_size <= MAX_FILE_BYTES, f"file exceeds {MAX_FILE_BYTES} bytes: {path}")
        content = stream.read(MAX_FILE_BYTES + 1)
        require(len(content) <= MAX_FILE_BYTES, f"file exceeds {MAX_FILE_BYTES} bytes: {path}")
        return content


def read_json(path):
    return json.loads(read_file(path).decode("utf-8"), object_pairs_hook=unique_fields)


def task_manifest(content):
    """Validate pinned task manifest bytes; return the manifest or raise ValueError."""
    manifest = json.loads(content, object_pairs_hook=unique_fields)
    require(isinstance(manifest, dict) and manifest.get("schemaVersion") == 1 and manifest.get("kind") == "specdoc-task-context", "unsupported task manifest")
    require(isinstance(manifest.get("id"), str) and re.fullmatch(r"[0-9a-f]{64}", manifest["id"]), "manifest needs a SHA256 identity")
    encoded = json.dumps({k: v for k, v in manifest.items() if k != "id"}, ensure_ascii=False, separators=(",", ":"))
    require(digest(encoded.encode("utf-8")) == manifest["id"], "manifest identity does not match its contents")
    implementation = manifest.get("implementation")
    require(isinstance(implementation, dict) and isinstance(implementation.get("repository"), str) and Path(implementation["repository"]).is_absolute(), "manifest needs an explicit implementation checkout")
    require(isinstance(implementation.get("revision"), str) and re.fullmatch(OBJECT_ID, implementation["revision"]), "manifest needs a full implementation revision")
    selection = manifest.get("selection")
    require(isinstance(selection, dict) and all(type(selection.get(key)) is bool for key in ("dependenciesComplete", "requirementsComplete")), "manifest needs explicit selection completeness")
    sources = manifest.get("sources")
    require(isinstance(sources, list) and sources, "manifest needs pinned sources")
    for item in sources:
        require(isinstance(item, dict) and isinstance(item.get("repository"), str) and Path(item["repository"]).is_absolute(), "manifest source needs an explicit checkout")
        require(isinstance(item.get("id"), str) and item["id"], "manifest source needs an ID")
        require(relative(item.get("path")), "manifest source path must be relative")
        for key in ("revision", "blob"):
            require(isinstance(item.get(key), str) and re.fullmatch(OBJECT_ID, item[key]), f"manifest source needs a full {key} ID")
        require(isinstance(item.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), "manifest source needs SHA256")
    require(isinstance(manifest.get("requiredGaps"), list), "manifest needs requiredGaps")
    return manifest


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
        require("expected_commit" not in merged or isinstance(merged["expected_commit"], str) and re.fullmatch(OBJECT_ID, merged["expected_commit"]), "expected_commit must be a full Git object ID")
    specdoc = data.get("specdoc")
    if specdoc is not None:
        require(isinstance(specdoc, dict) and set(specdoc) == {"checkout", "require_mcp"} and isinstance(specdoc["checkout"], str) and specdoc["checkout"] in checkouts and type(specdoc["require_mcp"]) is bool, "invalid specdoc fields")
    if "task_manifest" in data:
        absolute(data["task_manifest"], "task_manifest")
    return data


def git_version(git):
    try:
        output = subprocess.run([git, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.match(r"git version (\d+)\.(\d+)", output)
    return (int(match[1]), int(match[2])) if match else None


def git_run(git, root, arguments, config=()):
    # GIT_CONFIG_* keys, unlike -c, accept any filter driver name.
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
           "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": str(len(config))}
    for index, (key, value) in enumerate(config):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    try:
        result = subprocess.run([git, "--no-pager", "--no-optional-locks", "--no-lazy-fetch", "--no-replace-objects", "-C", str(root), *arguments],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=10, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


@functools.lru_cache(maxsize=None)
def command_overrides(git, root):
    """Blank the repository settings that make status run project commands."""
    keys = git_run(git, root, ("config", "-z", "--name-only", "--get-regexp", r"^filter\.")) or b""
    drivers = {os.fsdecode(key)[len("filter."):].rpartition(".")[0] for key in keys.split(b"\0") if key}
    config = [("core.fsmonitor", "false"), ("core.untrackedCache", "false")]
    for name in sorted(drivers):
        config.extend((f"filter.{name}.{key}", "") for key in ("clean", "smudge", "process"))
        config.append((f"filter.{name}.required", "false"))
    return tuple(config)


def git_read(git, root, *arguments):
    if not git:
        return None
    return git_run(git, root, arguments, command_overrides(git, root))


def file_identity(path):
    try:
        content = read_file(path)
        return {"path": str(path.resolve()), "sha256": digest(content), "status": "found"}
    except (OSError, ValueError) as error:
        return {"path": str(path), "status": "missing", "error": str(error)}


def checkout_report(name, value, git):
    root = absolute(value["path"], name)
    record = {"id": name, "path": str(root), "required": value["required"], "status": "missing", "head": None, "branch": None,
              "dirty": None, "tracked_changes": [], "untracked": []}
    if not git:
        record["guidance"] = f"Install Git {'.'.join(map(str, MINIMUM_GIT))} or later; nothing was cloned or selected automatically."
        return record
    top = git_read(git, root, "rev-parse", "--show-toplevel")
    if top is None:
        record["guidance"] = "Select an existing Git checkout explicitly; nothing was cloned or selected automatically."
        return record
    if Path(os.fsdecode(top).strip()).resolve() != root:
        record["guidance"] = "Select the Git checkout root, rather than a directory inside another checkout."
        return record
    head = git_read(git, root, "rev-parse", "--verify", "HEAD")
    branch = git_read(git, root, "symbolic-ref", "--quiet", "--short", "HEAD")
    # Submodules are skipped: their own configuration would apply inside them.
    status = git_read(git, root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all")
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
    if record["dirty"]:
        record["guidance"] = "Inspect the reported edits before reusing this checkout; no files were changed."
    elif record["branch"] is None:
        record["guidance"] = "HEAD is detached; no checkout was switched."
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
        content = git_read(git, root, "cat-file", "blob", blob.decode().strip()) if blob else None
        record["sources"].append({"path": path, "blob": blob.decode().strip() if blob else None,
                                  "sha256": digest(content) if content is not None else None,
                                  "status": "found" if content is not None else "missing"})
    if any(s["status"] == "missing" for s in record["sources"]):
        record["status"] = "missing"
    return record


def manifest_report(path):
    path = absolute(path, "task_manifest")
    try:
        content = read_file(path)
    except (OSError, ValueError) as error:
        return {"path": str(path), "status": "missing", "error": str(error), "guidance": "Pin the task manifest explicitly with the selected SpecDoc checkout."}
    record = {"path": str(path.resolve()), "sha256": digest(content)}
    try:
        manifest = task_manifest(content)
    except ValueError as error:
        return {**record, "status": "invalid", "error": str(error), "guidance": "Pin the task manifest again; its identity or structure is invalid."}
    record.update(status="unknown", id=manifest["id"], implementation=manifest["implementation"], selection=manifest["selection"],
                  sources=manifest["sources"], required_gaps=manifest["requiredGaps"],
                  guidance="Identity and structure only: SpecDoc must verify Git bytes, dependencies and completeness before acceptance.")
    return record


def inspect(config_path, tool_root):
    require(config_path.is_absolute(), "doctor --config must be an absolute path")
    config_path = config_path.resolve()
    data = load_config(config_path)
    roots = {name: absolute(value["path"], name) for name, value in data["checkouts"].items()}
    # Ignore relative PATH entries so an unrelated current directory cannot select Git.
    search = os.pathsep.join(p for p in os.get_exec_path() if Path(p).is_absolute())
    git = shutil.which("git", path=search)
    version = git_version(git) if git else None
    git_record = {"path": git, "version": ".".join(map(str, version)) if version else None,
                  "status": "missing" if not git else "found" if version and version >= MINIMUM_GIT else "unsupported"}
    if git_record["status"] != "found":
        git = None
    checkouts = [checkout_report(name, value, git) for name, value in data["checkouts"].items()]
    capabilities = []
    for capability in data.get("capabilities", []):
        root = roots[capability["checkout"]]
        checks = [{"kind": "file", "name": name, **file_identity(root / name)} for name in capability.get("files", [])]
        for name in capability.get("commands", []):
            path = shutil.which(name, path=search)
            checks.append({"kind": "command", "name": name, "path": path, "status": "found" if path else "missing", "version": "unknown"})
        capabilities.append({**capability, "checks": checks, "status": "missing" if any(c["status"] == "missing" for c in checks) else "unknown",
                             "guidance": capability.get("guidance") or "Select a checkout containing the declared files and provision missing executables separately; runtime readiness has not been established."})
    result = {"schema_version": 1, "kind": "workspace-doctor", "config": file_identity(config_path),
              "tool_catalog": file_identity(tool_root / "tools/oracles.json"), "git": git_record,
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
    if result.get("task_manifest", {}).get("status") in ("missing", "invalid"):
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
