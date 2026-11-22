// @exec: {"yield_time_ms": 120000, "max_output_tokens": 1800}
// Codex functions.exec host recipe, NOT a standalone Node/API script.
// Run this whole file in one host execution, without model turns between steps.
// The edit input must have been inspected with view_image before this recipe.
// Configure these paths for the installed skill and this one prepared photo.
const project = '/absolute/path/to/working-directory';
const python = '/absolute/path/to/venv/bin/python';
const skillScripts = '/absolute/path/to/restore-collectible-artwork/scripts';
const config = '/absolute/path/to/job.json';
const prompt = '/absolute/path/to/prompt.txt';
const editInput = '/absolute/path/to/prepared/edit-input.png';
const toolDir = '/absolute/path/to/tool-png-directory';
const jobDir = `/absolute/path/to/jobs/job-${Date.now()}`;
const quote = value => "'" + String(value).replaceAll("'", "'\\''") + "'";
const startArgs = [python, `${skillScripts}/artwork-job.py`, 'start',
  '--config', config, '--prompt', prompt, '--edit-input', editInput,
  '--tool-dir', toolDir, '--job-dir', jobDir];
const preflight = await tools.exec_command({cmd: startArgs.map(quote).join(' '), workdir: project, max_output_tokens: 2500});
if (preflight.exit_code !== 0) {text(preflight); exit();}
const job = JSON.parse(preflight.output);
const started = Date.now()/1000;
const generated = await tools.image_gen__imagegen({
  prompt: job.prompt, referenced_image_paths: [job.edit_input], transparent_background: job.transparent_background,
});
const ended = Date.now()/1000;
const finishArgs = [python, `${skillScripts}/artwork-job.py`, 'finish', '--job-dir', jobDir,
                    '--tool-started-unix', started, '--tool-ended-unix', ended];
const finished = await tools.exec_command({cmd: finishArgs.map(quote).join(' '), workdir: project, max_output_tokens: 1500});
generatedImage(generated);
text(finished);
if (finished.exit_code === 0) {
  const result = JSON.parse(finished.output);
  image((await tools.view_image({path: result.comparison})).image_url);
}
