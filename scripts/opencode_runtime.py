"""Version-aware isolated OpenCode invocations without changing user defaults."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from source_identity import file_hash


def runtime_identity(executable='opencode', cwd=None):
    probe_env = os.environ.copy()
    probe_env['PWD'] = str(Path(cwd or Path.cwd()).resolve())
    result = subprocess.run([executable, '--version'], cwd=cwd, env=probe_env, capture_output=True, text=True, timeout=15)
    version = result.stdout.strip()
    match = re.search(r'\b(?:v)?(\d+)\.(\d+)\.(\d+)', version)
    if result.returncode or not match:
        raise ValueError('Cannot determine OpenCode version; inspect --version')
    major = int(match[1])
    if major not in (1, 2):
        raise ValueError('Unsupported OpenCode major version; verify CLI/config compatibility first')
    binary = shutil.which(executable)
    identity = {'version': version, 'major': major, 'config_files': {},
                'executable_sha256': file_hash(Path(binary).resolve()) if binary else None}
    if major == 2:
        # This command can contain credentials in arbitrary provider/plugin
        # fields. Never log it: retain only config file identities and model ID.
        probe = subprocess.run([executable, 'debug', 'config'], cwd=cwd, env=probe_env, capture_output=True, text=True, timeout=15)
        if probe.returncode:
            raise ValueError('OpenCode configuration discovery failed')
        for entry in json.loads(probe.stdout):
            path = Path(entry.get('path', ''))
            if entry.get('type') == 'document' and path.is_file():
                identity['config_files'][str(path)] = file_hash(path)
            model = entry.get('info', {}).get('model')
            if isinstance(model, str):
                identity['configured_model'] = model
    else:
        # V1 has no v2 config-document inventory. Capture known discovery files
        # and state the scope instead of claiming the effective config is known.
        base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'opencode'
        candidates = [base/'opencode.json', base/'opencode.jsonc']
        configured = os.environ.get('OPENCODE_CONFIG')
        if configured:
            candidates.append(Path(configured).expanduser())
        folder = Path(cwd or Path.cwd()).resolve()
        for parent in (folder, *folder.parents):
            candidates.extend(parent/name for name in ('opencode.json', 'opencode.jsonc'))
        identity['config_files'] = {str(p.resolve()): file_hash(p) for p in candidates if p.is_file()}
        identity['config_discovery_scope'] = 'known v1 config locations; effective plugin/provider environment is not introspected'
    # Store no environment values: config overlays and endpoint changes still
    # invalidate a resume, while credentials and transport bodies never appear.
    import hashlib
    identity['environment_sha256'] = {key: hashlib.sha256(value.encode()).hexdigest()
        for key, value in sorted(os.environ.items())
        if key.startswith('OPENCODE_') and not any(word in key for word in ('KEY', 'TOKEN', 'PASSWORD', 'SECRET'))}
    return identity


def invocation(executable, job, config, prompt, images, agent, title):
    job = Path(job).resolve()
    runtime = runtime_identity(executable, job)
    env = os.environ.copy()
    # OpenCode v2 derives its Location from the inherited logical directory.
    # Popen(cwd=...) alone leaves the coordinator's PWD in the child env.
    env['PWD'] = str(job)
    if runtime['major'] == 1:
        env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
        env['OPENCODE_PERMISSION'] = json.dumps(config['permission'])
        import hashlib
        runtime['job_config_sha256'] = hashlib.sha256(env['OPENCODE_CONFIG_CONTENT'].encode()).hexdigest()
        command = [executable, 'run', '--pure', '--format', 'json', '--dir', str(job)]
    else:
        permissions = [{'action': '*', 'resource': '*', 'effect': 'deny'}]
        for action, rules in config['permission'].items():
            if action == '*':
                continue
            rules = rules if isinstance(rules, dict) else {'*': rules}
            permissions.extend({'action': action, 'resource': pattern, 'effect': effect}
                               for pattern, effect in rules.items())
        # V2 Code Mode exposes tools through execute; nested read/edit/shell
        # operations still enforce their own scoped rules.
        permissions.append({'action': 'execute', 'resource': '*', 'effect': 'allow'})
        source_agent = config['agent'][agent]
        worker = {key: source_agent[key] for key in ('description', 'mode', 'steps', 'model') if key in source_agent}
        worker.update(system=source_agent['prompt'], permissions=permissions)
        actual = {'$schema': 'https://opencode.ai/config.json', 'agents': {agent: worker},
                  'permissions': permissions, 'snapshots': False,
                  'policies': [{'action': action, 'resource': '*', 'effect': 'deny'}
                               for action in ('plugin.use', 'mcp.use', 'skill.use')]}
        if config.get('model'):
            actual['model'] = config['model']
        if config.get('provider'):
            actual['providers'] = {provider: {'models': {model: {'settings': item.get('options', {})}
                                                       for model, item in value.get('models', {}).items()}}
                                   for provider, value in config['provider'].items()}
        path = job / '.opencode' / 'opencode.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, ensure_ascii=False, indent=2))
        runtime['job_config_sha256'] = file_hash(path)
        # A private server reads this task's project config. The shared server
        # may predate these permissions or carry another task's configuration.
        command = [executable, 'run', '--standalone', '--format', 'json']
        env.pop('OPENCODE_CONFIG_CONTENT', None)
        env.pop('OPENCODE_PERMISSION', None)
    command += ['--agent', agent, '--title', title]
    if config.get('model'):
        command += ['--model', config['model']]
    for image in images:
        command += ['--file', str(image)]
    command += ['--', prompt]
    return command, env, runtime


def safe_diagnostic(stderr):
    """Keep CLI failure categories, never transport bodies or configuration dumps."""
    patterns = {
        'unsupported_cli_flag': r'unknown (?:flag|option)|unrecognized argument|unexpected argument',
        'invalid_configuration': r'config.*(?:invalid|error)|unknown.*(?:field|key)',
        'authentication_failed': r'unauthorized|authentication|invalid.api.key|\b401\b',
        'rate_limited': r'rate.limit|\b429\b',
        'connection_failed': r'connection|ECONN|ENOTFOUND|network|fetch failed',
        'permission_denied': r'permission.*(?:denied|reject)|not allowed',
        'context_limit': r'context.*(?:length|limit)|too many tokens',
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, stderr, re.I)]


def session_metadata(executable, job, sessions, runtime):
    """Retain known v2 session metadata; never persist exported text/images.

    Tokens stay as returned because reasoning semantics vary across versions.
    """
    if runtime.get('major') != 2:
        return {'session_metadata_status': 'not_supported_by_adapter'}
    env = os.environ.copy()
    env['PWD'] = str(Path(job).resolve())
    models, records, errors = set(), [], []
    for sid in sessions:
        if not isinstance(sid, str) or not re.fullmatch(r'ses_[A-Za-z0-9_-]+', sid):
            continue
        try:
            result = subprocess.run([executable, 'session', 'export', '--standalone', sid], cwd=job,
                                    env=env, capture_output=True, text=True, timeout=20)
            if result.returncode:
                errors.append('session_export_failed')
                continue
            data = json.loads(result.stdout)
            for item in data.get('messages', []):
                message = item.get('info', item)
                if message.get('type', message.get('role')) != 'assistant':
                    continue
                model = message.get('model', {})
                if isinstance(model, dict) and isinstance(model.get('id'), str) and isinstance(model.get('providerID'), str):
                    models.add((model['providerID'], model['id']))
                tokens = message.get('tokens')
                selected = None
                if isinstance(tokens, dict):
                    selected = {k: tokens[k] for k in ('input', 'output', 'reasoning', 'total')
                                if type(tokens.get(k)) in (int, float)}
                    cache = tokens.get('cache', {})
                    selected['cache'] = {k: cache[k] for k in ('read', 'write')
                                         if isinstance(cache, dict) and type(cache.get(k)) in (int, float)}
                records.append({'session_id': sid, 'message_id': message.get('id'),
                                'tokens_as_returned': selected,
                                'opencode_reported_cost': message.get('cost') if type(message.get('cost')) in (int, float) else None,
                                'finish': message.get('finish')})
        except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
            errors.append(type(exc).__name__)
    return {'actual_models': [{'provider_id': p, 'model_id': m} for p, m in sorted(models)],
            'session_usage': records, 'session_metadata_errors': errors,
            'session_usage_partial': bool(errors) or not records or any(
                not item['tokens_as_returned'] or item['finish'] in {None, 'error', 'cancelled'} for item in records),
            'session_metadata_status': 'read' if records and not errors else 'partial',
            'usage_semantics': 'tokens/cost as returned by installed OpenCode; no inferred totals or provider billing reconciliation'}
