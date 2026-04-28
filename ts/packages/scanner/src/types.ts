/**
 * OpenAI function-calling shape — the common interchange format
 * consumed by `sponsio/discovery/extractors/tool_inventory.py`.
 */
export interface OpenAITool {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: JSONSchema;
  };
}

export interface ScanOutput {
  /** Tools found in the scanned source files. */
  tools: OpenAITool[];
  /** Per-file diagnostics — empty on a clean run. */
  diagnostics: Diagnostic[];
  /** Which framework-specific extractor produced each tool. */
  provenance: Record<string, ToolProvenance>;
}

export interface ToolProvenance {
  filepath: string;
  line: number;
  extractor: "vercel_ai" | "langchain_js" | "generic" | "generic_decorator";
}

export interface Diagnostic {
  filepath: string;
  line: number;
  level: "warn" | "error";
  message: string;
}

/** Minimal JSON Schema we emit — always a top-level `object`. */
export interface JSONSchema {
  type: "object";
  properties: Record<string, JSONSchemaProperty>;
  required?: string[];
}

export interface JSONSchemaProperty {
  type: JSONSchemaType;
  description?: string;
  items?: JSONSchemaProperty;
  enum?: (string | number | boolean)[];
  properties?: Record<string, JSONSchemaProperty>;
}

export type JSONSchemaType =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "array"
  | "object"
  | "null";
