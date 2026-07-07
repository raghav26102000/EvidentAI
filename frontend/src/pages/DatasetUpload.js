import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { toast } from "sonner";
import { UploadCloud, FileWarning, ShieldCheck, Cpu, Lock } from "lucide-react";

const MAX_BYTES = 50 * 1024 * 1024;
const ALLOWED = [".csv", ".xlsx"];

const STEPS = [
    { key: "validate", label: "Structural validation", desc: "Extension allowlist · macro / OLE reject · CSV formula check" },
    { key: "scan", label: "Malware scan", desc: "ClamAV INSTREAM · fail-closed on error" },
    { key: "encrypt", label: "Encrypt (AES-256-GCM)", desc: "Per-tenant DEK, fresh 96-bit nonce" },
    { key: "store", label: "Persist ciphertext", desc: "Object storage — never plaintext" },
    { key: "profile", label: "Sandboxed profile", desc: "rlimits · timeout · no-network" },
];

export default function DatasetUpload() {
    const [file, setFile] = useState(null);
    const [busy, setBusy] = useState(false);
    const [progress, setProgress] = useState(0);
    const [err, setErr] = useState(null);
    const inputRef = useRef(null);
    const nav = useNavigate();

    const validate = (f) => {
        if (!f) return "No file";
        const lower = f.name.toLowerCase();
        if (!ALLOWED.some((e) => lower.endsWith(e))) {
            return `Only ${ALLOWED.join(", ")} are allowed (got ${f.name}).`;
        }
        if (f.size > MAX_BYTES) {
            return `File exceeds ${MAX_BYTES / (1024 * 1024)}MB limit.`;
        }
        if (f.size === 0) return "File is empty.";
        return null;
    };

    const onSelect = (f) => {
        setErr(null);
        const v = validate(f);
        if (v) {
            setErr(v);
            setFile(null);
            return;
        }
        setFile(f);
    };

    const onDrop = useCallback((e) => {
        e.preventDefault();
        const f = e.dataTransfer?.files?.[0];
        if (f) onSelect(f);
    }, []);

    const submit = async () => {
        if (!file) return;
        setBusy(true);
        setProgress(0);
        setErr(null);
        try {
            const form = new FormData();
            form.append("file", file);
            const r = await api.post("/datasets", form, {
                onUploadProgress: (evt) => {
                    if (evt.total) setProgress(Math.round((evt.loaded / evt.total) * 100));
                },
            });
            toast.success(`Dataset "${r.data.original_filename}" is ${r.data.status}`);
            nav(`/datasets/${r.data.id}`);
        } catch (e) {
            const d = e?.response?.data?.detail;
            setErr(typeof d === "string" ? d : JSON.stringify(d) || "Upload failed");
            toast.error("Upload rejected");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6" data-testid="upload-page">
            <div className="lg:col-span-3 space-y-6">
                <div>
                    <div className="label-caps mb-1">Upload</div>
                    <h1 className="font-heading text-3xl sm:text-4xl font-bold text-white">
                        Ingest a dataset
                    </h1>
                </div>

                <div
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={onDrop}
                    onClick={() => inputRef.current?.click()}
                    className="panel border-dashed border-2 border-[hsl(var(--border))] hover:border-[hsl(var(--primary))] cursor-pointer p-12 flex flex-col items-center justify-center text-center transition-colors"
                    data-testid="upload-dropzone"
                >
                    <UploadCloud size={36} className="text-[hsl(var(--muted-foreground))] mb-3" strokeWidth={1.4} />
                    <div className="font-heading text-lg font-medium text-white">
                        Drop a CSV or .xlsx here
                    </div>
                    <div className="text-xs text-[hsl(var(--muted-foreground))] mt-1 font-mono">
                        or click to browse · max 50 MB
                    </div>
                    <input
                        ref={inputRef}
                        data-testid="upload-file-input"
                        type="file"
                        accept=".csv,.xlsx"
                        className="hidden"
                        onChange={(e) => e.target.files?.[0] && onSelect(e.target.files[0])}
                    />
                </div>

                {file && (
                    <div className="panel p-4 flex items-center justify-between" data-testid="upload-selection">
                        <div>
                            <div className="text-sm text-white font-mono" data-testid="upload-filename">{file.name}</div>
                            <div className="text-xs text-[hsl(var(--muted-foreground))] font-mono">
                                {(file.size / 1024).toFixed(1)} KB
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                data-testid="upload-clear-button"
                                onClick={() => {
                                    setFile(null);
                                    setErr(null);
                                }}
                                className="text-xs px-2.5 py-1.5 border border-[hsl(var(--border))] hover:bg-[hsl(var(--secondary))] rounded-sm"
                            >
                                Clear
                            </button>
                            <button
                                data-testid="upload-submit-button"
                                onClick={submit}
                                disabled={busy}
                                className="text-xs px-3 py-1.5 bg-[hsl(var(--primary))] hover:bg-[hsl(217,100%,45%)] disabled:opacity-60 text-white rounded-sm"
                            >
                                {busy ? `Uploading ${progress}%…` : "Encrypt & upload"}
                            </button>
                        </div>
                    </div>
                )}

                {err && (
                    <div
                        className="panel p-3 text-sm flex items-start gap-2"
                        style={{ borderColor: "hsl(0,84%,40%)", color: "hsl(0,84%,72%)" }}
                        data-testid="upload-error"
                    >
                        <FileWarning size={16} className="flex-shrink-0 mt-0.5" />
                        <div className="font-mono text-xs">{err}</div>
                    </div>
                )}
            </div>

            <aside className="lg:col-span-2 space-y-3" data-testid="upload-pipeline-panel">
                <div className="label-caps">Ingestion pipeline</div>
                <div className="panel divide-y divide-[hsl(var(--border))]">
                    {STEPS.map((s, i) => (
                        <div key={s.key} className="p-3 flex items-start gap-3">
                            <div className="flex-shrink-0 w-6 h-6 border border-[hsl(var(--border))] flex items-center justify-center text-xs font-mono text-[hsl(var(--muted-foreground))]">
                                {String(i + 1).padStart(2, "0")}
                            </div>
                            <div>
                                <div className="text-sm text-white">{s.label}</div>
                                <div className="text-xs text-[hsl(var(--muted-foreground))] font-mono mt-0.5">
                                    {s.desc}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
                <div className="grid grid-cols-3 gap-2 pt-2">
                    <div className="panel p-3 text-center">
                        <ShieldCheck size={16} className="mx-auto text-[hsl(var(--success))]" />
                        <div className="label-caps mt-1">RLS</div>
                    </div>
                    <div className="panel p-3 text-center">
                        <Lock size={16} className="mx-auto text-[hsl(var(--success))]" />
                        <div className="label-caps mt-1">AES-GCM</div>
                    </div>
                    <div className="panel p-3 text-center">
                        <Cpu size={16} className="mx-auto text-[hsl(var(--success))]" />
                        <div className="label-caps mt-1">SANDBOX</div>
                    </div>
                </div>
            </aside>
        </div>
    );
}
