import { useState, useRef, useCallback } from 'react';

export interface FileUploadProps {
  accept: string;
  onFile: (file: File) => void;
  label?: string;
  sublabel?: string;
}

export default function FileUpload({ accept, onFile, label, sublabel }: FileUploadProps) {
  const [dragOver, setDragOver] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback((file: File) => {
    setFileName(`${file.name} (${(file.size / 1024).toFixed(1)} KB)`);
    onFile(file);
  }, [onFile]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }, [handleFile]);

  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer border-dashed border-2 rounded-xl p-8 text-center transition-colors ${
          dragOver
            ? 'border-brand bg-brand/5'
            : 'border-surface-300 dark:border-surface-700 hover:border-surface-400 dark:hover:border-surface-600'
        }`}
      >
        <svg className="w-8 h-8 text-muted mx-auto mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
        </svg>
        <p className="text-sm text-zinc-700 dark:text-zinc-300 font-medium mb-1">
          {label ?? 'Drop a file here or click to browse'}
        </p>
        <p className="text-xs text-muted">
          {sublabel ?? `Accepts ${accept}`}
        </p>
        <input ref={inputRef} type="file" accept={accept} className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
      </div>
      {fileName && (
        <div className="mt-2 flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-500/5 border border-emerald-500/20">
          <svg className="w-3.5 h-3.5 text-emerald-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4" />
          </svg>
          <span className="text-xs text-emerald-400 font-mono truncate">{fileName}</span>
        </div>
      )}
    </div>
  );
}
