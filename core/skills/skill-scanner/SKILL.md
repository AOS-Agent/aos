---
name: scan-skill
description: Scans external skills, plugins, and code for prompt injection attacks, data exfiltration, and security risks before installation.
argument-hint: <github-url-or-skill-name>
---

# Skill: Prompt Injection Scanner

Scans external skills, plugins, and code for prompt injection attacks, data exfiltration, and security risks before installation.

## When to Use
- Before installing any skill from skills.sh, claude-plugins.dev, GitHub, or any external source
- When reviewing MCP server configurations
- When auditing existing installed skills

## Critical Patterns (auto-reject)
```
/ignore.*(previous|system|above).*(instruction|prompt|message)/i
/you are now/i
/admin mode|developer mode|debug mode|jailbreak/i
/override.*safety|bypass.*safety/i
/base64|atob|btoa/i — in non-obvious contexts
/curl.*-d|wget.*--post|fetch\(.*method.*POST/i — data exfiltration
/\.env|\.ssh|\.aws|credentials|api.?key|secret/i — credential access
/eval\(|exec\(|Function\(|child_process/i — dynamic execution
```

## Warning Patterns (flag for review)
```
/https?:\/\/(?!localhost|127\.0\.0\.1)/i — external network calls
/fs\.write|writeFile|appendFile/i — file system writes
/\.min\.js|\.min\.css/ — minified code (can't inspect)
/process\.env/ — environment variable access
/chmod|chown|sudo/ — permission changes
```

## Scan Procedure
1. Clone/download to /tmp/skill-scan/
2. Count files, lines, detect languages
3. Grep all files against critical patterns
4. Grep all files against warning patterns
5. Check for hidden files (.hidden, .*)
6. Check for unusually large files (> 1MB for a skill)
7. Report findings with file:line references
8. Verdict: SAFE / REVIEW NEEDED / REJECTED
