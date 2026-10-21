export type ProbeOptions = {
  name?: string;
  timeout: number;
  cwd?: string;
  env?: Record<string, string | undefined>;
  maxBuffer?: number;
};

export function runProbe(
  args: readonly string[],
  options: ProbeOptions,
): Promise<{ stdout: string; stderr: string }>;
