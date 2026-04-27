use globset::{Glob, GlobSet, GlobSetBuilder};
use regex::{Regex, RegexBuilder};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::time::UNIX_EPOCH;
use walkdir::WalkDir;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileRecord {
    pub path: String,
    pub hash: String,
    pub size: u64,
    pub mtime: f64,
    pub language: String,
    pub unchanged: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SymbolRecord {
    pub id: String,
    pub path: String,
    pub kind: String,
    pub name: String,
    pub line: usize,
    pub signature: String,
    pub fingerprint: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EdgeRecord {
    pub source: String,
    pub target: String,
    pub relation: String,
    pub path: String,
    pub line: Option<usize>,
    pub confidence: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexRecord {
    pub files: Vec<FileRecord>,
    pub symbols: Vec<SymbolRecord>,
    pub edges: Vec<EdgeRecord>,
    pub skipped_sensitive: usize,
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct KnownFile {
    pub hash: String,
    pub size: u64,
    pub mtime: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ScanConfig {
    pub supported_extensions: HashMap<String, String>,
    pub max_file_bytes: u64,
    pub skip_dirs: Vec<String>,
    pub skip_files: Vec<String>,
    pub ignore_patterns: Vec<String>,
    pub sensitive_patterns: Vec<String>,
    pub known_files: HashMap<String, KnownFile>,
}

impl Default for ScanConfig {
    fn default() -> Self {
        Self {
            supported_extensions: default_supported_extensions(),
            max_file_bytes: 2_000_000,
            skip_dirs: default_skip_dirs(),
            skip_files: default_skip_files(),
            ignore_patterns: Vec::new(),
            sensitive_patterns: default_sensitive_patterns(),
            known_files: HashMap::new(),
        }
    }
}

struct Extractors {
    py: Regex,
    js: Regex,
    go: Regex,
    rs: Regex,
    java: Regex,
    import: Regex,
    call: Regex,
}

impl Extractors {
    fn new() -> Self {
        Self {
            py: Regex::new(r"^\s*(async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)").unwrap(),
            js: Regex::new(
                r"^\s*(export\s+)?(async\s+)?(function|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            )
            .unwrap(),
            go: Regex::new(r"^\s*(func|type)\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)")
                .unwrap(),
            rs: Regex::new(r"^\s*(pub\s+)?(fn|struct|enum|trait|impl)\s+([A-Za-z_][A-Za-z0-9_]*)?")
                .unwrap(),
            java: Regex::new(
                r"^\s*(public|private|protected)?\s*(static\s+)?(class|interface|enum|void|[A-Za-z0-9_<>\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)",
            )
            .unwrap(),
            import: Regex::new(
                r#"^\s*(import\s+.+|from\s+\S+\s+import\s+.+|use\s+.+|require\s*\(.+|#include\s+[<"].+[>"]|package\s+\S+)"#,
            )
            .unwrap(),
            call: Regex::new(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(").unwrap(),
        }
    }
}

pub fn scan_repo(root: &Path) -> std::io::Result<IndexRecord> {
    scan_repo_with_config(root, &ScanConfig::default())
}

pub fn scan_repo_with_config(root: &Path, config: &ScanConfig) -> std::io::Result<IndexRecord> {
    let mut files = Vec::new();
    let mut symbols = Vec::new();
    let mut edges = Vec::new();
    let mut errors = Vec::new();
    let mut skipped_sensitive = 0usize;
    let ignore_set = compile_ignore_set(&config.ignore_patterns);
    let sensitive = compile_sensitive_patterns(&config.sensitive_patterns);
    let extractors = Extractors::new();

    for entry in WalkDir::new(root)
        .into_iter()
        .filter_entry(|e| !is_skip(e.path(), root, config, ignore_set.as_ref()))
    {
        let entry = match entry {
            Ok(e) => e,
            Err(err) => {
                errors.push(err.to_string());
                continue;
            }
        };
        if !entry.file_type().is_file() {
            continue;
        }
        let path = entry.path();
        let rel = relative_path(root, path);
        if config
            .skip_files
            .iter()
            .any(|item| item == entry.file_name().to_string_lossy().as_ref())
        {
            continue;
        }
        if is_ignored_path(
            &rel,
            entry.file_name().to_string_lossy().as_ref(),
            ignore_set.as_ref(),
        ) {
            continue;
        }
        if is_sensitive_path(path, &sensitive) {
            skipped_sensitive += 1;
            continue;
        }
        let Some(language) = language_for(path, &config.supported_extensions) else {
            continue;
        };
        let metadata = match entry.metadata() {
            Ok(data) => data,
            Err(err) => {
                errors.push(format!("{rel}: {err}"));
                continue;
            }
        };
        let size = metadata.len();
        if size > config.max_file_bytes {
            continue;
        }
        let mtime = modified_seconds(&metadata);
        if let Some(old) = config.known_files.get(&rel) {
            if old.size == size && (old.mtime - mtime).abs() < 0.000001 {
                files.push(FileRecord {
                    path: rel,
                    hash: old.hash.clone(),
                    size,
                    mtime,
                    language,
                    unchanged: true,
                });
                continue;
            }
        }
        let bytes = match fs::read(path) {
            Ok(data) => data,
            Err(err) => {
                errors.push(format!("{rel}: {err}"));
                continue;
            }
        };
        let hash = sha256_hex(&bytes);
        files.push(FileRecord {
            path: rel.clone(),
            hash: hash.clone(),
            size,
            mtime,
            language: language.clone(),
            unchanged: false,
        });
        let text = String::from_utf8_lossy(&bytes);
        let (file_symbols, file_edges) =
            extract_symbols_edges(&rel, &hash, &text, &language, &extractors);
        symbols.extend(file_symbols);
        edges.extend(file_edges);
    }

    Ok(IndexRecord {
        files,
        symbols,
        edges,
        skipped_sensitive,
        errors,
    })
}

fn extract_symbols_edges(
    path: &str,
    digest: &str,
    text: &str,
    language: &str,
    extractors: &Extractors,
) -> (Vec<SymbolRecord>, Vec<EdgeRecord>) {
    let mut symbols = Vec::new();
    let mut edges = Vec::new();
    let file_id = stable_id(&["file", path]);
    symbols.push(SymbolRecord {
        id: file_id.clone(),
        path: path.to_string(),
        kind: "file".to_string(),
        name: Path::new(path)
            .file_name()
            .map(|name| name.to_string_lossy().to_string())
            .unwrap_or_else(|| path.to_string()),
        line: 1,
        signature: path.to_string(),
        fingerprint: digest.chars().take(16).collect(),
    });
    let mut current_container = file_id.clone();
    let mut defined_names: HashMap<String, String> = HashMap::new();
    for (idx, line) in text.lines().enumerate() {
        let line_no = idx + 1;
        if let Some((kind, name, signature)) = extract_symbol_from_line(line, language, extractors)
        {
            let symbol_id = stable_id(&[&kind, path, &name, &line_no.to_string()]);
            let fingerprint = sha256_short(&format!("{digest}:{line_no}:{signature}"), 16);
            symbols.push(SymbolRecord {
                id: symbol_id.clone(),
                path: path.to_string(),
                kind,
                name: name.clone(),
                line: line_no,
                signature,
                fingerprint,
            });
            edges.push(EdgeRecord {
                source: file_id.clone(),
                target: symbol_id.clone(),
                relation: "contains".to_string(),
                path: path.to_string(),
                line: Some(line_no),
                confidence: "EXTRACTED".to_string(),
            });
            current_container = symbol_id.clone();
            defined_names.insert(name.to_lowercase(), symbol_id);
        }
        if let Some(import) = extractors
            .import
            .captures(line)
            .and_then(|captures| captures.get(1))
        {
            edges.push(EdgeRecord {
                source: file_id.clone(),
                target: stable_id(&["external", &normalize_import(import.as_str())]),
                relation: "imports".to_string(),
                path: path.to_string(),
                line: Some(line_no),
                confidence: "EXTRACTED".to_string(),
            });
        }
        for call in extractors.call.captures_iter(line) {
            let Some(name) = call.get(1).map(|item| item.as_str().to_lowercase()) else {
                continue;
            };
            let Some(target) = defined_names.get(&name) else {
                continue;
            };
            if target != &current_container {
                edges.push(EdgeRecord {
                    source: current_container.clone(),
                    target: target.clone(),
                    relation: "calls".to_string(),
                    path: path.to_string(),
                    line: Some(line_no),
                    confidence: "INFERRED".to_string(),
                });
            }
        }
    }
    (symbols, edges)
}

fn extract_symbol_from_line(
    line: &str,
    language: &str,
    extractors: &Extractors,
) -> Option<(String, String, String)> {
    if language == "python" {
        return extractors.py.captures(line).map(|captures| {
            let raw = captures.get(1).unwrap().as_str();
            let kind = if raw == "class" { "class" } else { "function" };
            (
                kind.to_string(),
                captures.get(2).unwrap().as_str().to_string(),
                line.trim().to_string(),
            )
        });
    }
    if language == "javascript" || language == "typescript" {
        return extractors.js.captures(line).map(|captures| {
            (
                captures.get(3).unwrap().as_str().to_string(),
                captures.get(4).unwrap().as_str().to_string(),
                line.trim().to_string(),
            )
        });
    }
    if language == "go" {
        return extractors.go.captures(line).map(|captures| {
            (
                captures.get(1).unwrap().as_str().to_string(),
                captures.get(2).unwrap().as_str().to_string(),
                line.trim().to_string(),
            )
        });
    }
    if language == "rust" {
        return extractors.rs.captures(line).and_then(|captures| {
            captures.get(3).map(|name| {
                (
                    captures.get(2).unwrap().as_str().to_string(),
                    name.as_str().to_string(),
                    line.trim().to_string(),
                )
            })
        });
    }
    if matches!(language, "java" | "csharp" | "kotlin" | "swift") {
        return extractors.java.captures(line).map(|captures| {
            (
                captures.get(3).unwrap().as_str().to_string(),
                captures.get(4).unwrap().as_str().to_string(),
                line.trim().to_string(),
            )
        });
    }
    None
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn sha256_short(text: &str, chars: usize) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    format!("{:x}", hasher.finalize())
        .chars()
        .take(chars)
        .collect()
}

fn stable_id(parts: &[&str]) -> String {
    sha256_short(&parts.join("::"), 20)
}

fn normalize_import(text: &str) -> String {
    let mut value = text.trim().trim_end_matches(';').to_string();
    for prefix in ["from ", "import ", "use ", "package "] {
        if let Some(rest) = value.strip_prefix(prefix) {
            value = rest.to_string();
            break;
        }
    }
    value = value.replace(" import ", ".");
    value
        .split(|ch: char| ch.is_whitespace() || ch == ',' || ch == '(' || ch == ';')
        .next()
        .unwrap_or("")
        .trim_matches(|ch| matches!(ch, '"' | '\'' | '<' | '>'))
        .to_string()
}

fn is_skip(path: &Path, root: &Path, config: &ScanConfig, ignore_set: Option<&GlobSet>) -> bool {
    if path == root {
        return false;
    }
    if path.components().any(|part| {
        let value = part.as_os_str().to_string_lossy();
        config.skip_dirs.iter().any(|skip| skip == value.as_ref())
    }) {
        return true;
    }
    let rel = relative_path(root, path);
    let name = path
        .file_name()
        .map(|item| item.to_string_lossy().to_string())
        .unwrap_or_default();
    is_ignored_path(&rel, &name, ignore_set)
}

fn is_ignored_path(rel: &str, file_name: &str, ignore_set: Option<&GlobSet>) -> bool {
    let Some(ignore_set) = ignore_set else {
        return false;
    };
    ignore_set.is_match(rel) || ignore_set.is_match(file_name)
}

fn compile_ignore_set(patterns: &[String]) -> Option<GlobSet> {
    let mut builder = GlobSetBuilder::new();
    let mut added = false;
    for raw in patterns {
        let pattern = raw.trim().trim_start_matches('/');
        if pattern.is_empty() {
            continue;
        }
        if let Ok(glob) = Glob::new(pattern) {
            builder.add(glob);
            added = true;
        }
        if !pattern.contains('/') {
            if let Ok(glob) = Glob::new(&format!("**/{pattern}")) {
                builder.add(glob);
                added = true;
            }
        }
    }
    if added {
        builder.build().ok()
    } else {
        None
    }
}

fn compile_sensitive_patterns(patterns: &[String]) -> Vec<Regex> {
    patterns
        .iter()
        .filter_map(|pattern| {
            RegexBuilder::new(pattern)
                .case_insensitive(true)
                .build()
                .ok()
        })
        .collect()
}

fn is_sensitive_path(path: &Path, patterns: &[Regex]) -> bool {
    let text = path.to_string_lossy();
    patterns.iter().any(|pattern| pattern.is_match(&text))
}

fn language_for(path: &Path, extensions: &HashMap<String, String>) -> Option<String> {
    let ext = path.extension()?.to_string_lossy().to_lowercase();
    extensions.get(&format!(".{ext}")).cloned()
}

fn relative_path(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

fn modified_seconds(metadata: &fs::Metadata) -> f64 {
    metadata
        .modified()
        .ok()
        .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
        .map(|value| value.as_secs_f64())
        .unwrap_or(0.0)
}

fn default_supported_extensions() -> HashMap<String, String> {
    [
        (".py", "python"),
        (".js", "javascript"),
        (".jsx", "javascript"),
        (".ts", "typescript"),
        (".tsx", "typescript"),
        (".go", "go"),
        (".rs", "rust"),
        (".java", "java"),
        (".c", "c"),
        (".h", "c"),
        (".cpp", "cpp"),
        (".hpp", "cpp"),
        (".rb", "ruby"),
        (".cs", "csharp"),
        (".kt", "kotlin"),
        (".swift", "swift"),
        (".php", "php"),
        (".md", "markdown"),
        (".mdx", "markdown"),
        (".txt", "text"),
        (".rst", "text"),
        (".toml", "config"),
        (".yaml", "config"),
        (".yml", "config"),
        (".json", "config"),
        (".ini", "config"),
        (".sql", "sql"),
        (".sh", "shell"),
        (".ps1", "powershell"),
    ]
    .into_iter()
    .map(|(key, value)| (key.to_string(), value.to_string()))
    .collect()
}

fn default_skip_dirs() -> Vec<String> {
    [
        ".git",
        ".hg",
        ".svn",
        ".memographix",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "node_modules",
        "site-packages",
        "dist",
        "build",
        "target",
        "coverage",
        ".next",
        ".turbo",
    ]
    .into_iter()
    .map(str::to_string)
    .collect()
}

fn default_skip_files() -> Vec<String> {
    [
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "poetry.lock",
        "Pipfile.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
    ]
    .into_iter()
    .map(str::to_string)
    .collect()
}

fn default_sensitive_patterns() -> Vec<String> {
    [
        r"(^|[\\/])\.(env|envrc)(\.|$)",
        r"\.(pem|key|p12|pfx|cert|crt|der|p8)$",
        r"(credential|secret|passwd|password|token|private_key)",
        r"(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.pub)?$",
        r"(\.netrc|\.pgpass|\.htpasswd)$",
    ]
    .into_iter()
    .map(str::to_string)
    .collect()
}

#[cfg(feature = "extension-module")]
use pyo3::prelude::*;

#[cfg(feature = "extension-module")]
#[pyfunction]
fn scan_repo_json(root: String) -> PyResult<String> {
    let data = scan_repo(Path::new(&root))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    serde_json::to_string(&data)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

#[cfg(feature = "extension-module")]
#[pyfunction]
fn scan_repo_config_json(root: String, config_json: String) -> PyResult<String> {
    let config: ScanConfig = serde_json::from_str(&config_json)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    let data = scan_repo_with_config(Path::new(&root), &config)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    serde_json::to_string(&data)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

#[cfg(feature = "extension-module")]
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(scan_repo_json, m)?)?;
    m.add_function(wrap_pyfunction!(scan_repo_config_json, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_repo(name: &str) -> std::path::PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("memographix-{name}-{nonce}"));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn scans_symbols_and_skips_sensitive_files() {
        let root = temp_repo("scan");
        fs::write(root.join("app.py"), "def route():\n    return True\n").unwrap();
        fs::write(root.join("secret.py"), "TOKEN='not-real'\n").unwrap();

        let result = scan_repo(&root).unwrap();
        let paths: Vec<_> = result.files.iter().map(|file| file.path.as_str()).collect();
        let names: Vec<_> = result
            .symbols
            .iter()
            .map(|symbol| symbol.name.as_str())
            .collect();

        assert_eq!(paths, vec!["app.py"]);
        assert!(names.contains(&"route"));
        assert_eq!(result.skipped_sensitive, 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn marks_known_unchanged_files_without_reextracting_symbols() {
        let root = temp_repo("incremental");
        fs::write(root.join("app.py"), "def route():\n    return True\n").unwrap();
        let first = scan_repo(&root).unwrap();
        let config = ScanConfig {
            known_files: first
                .files
                .iter()
                .map(|file| {
                    (
                        file.path.clone(),
                        KnownFile {
                            hash: file.hash.clone(),
                            size: file.size,
                            mtime: file.mtime,
                        },
                    )
                })
                .collect(),
            ..Default::default()
        };

        let second = scan_repo_with_config(&root, &config).unwrap();

        assert_eq!(second.files.len(), 1);
        assert!(second.files[0].unchanged);
        assert!(second.symbols.is_empty());
        fs::remove_dir_all(root).unwrap();
    }
}
