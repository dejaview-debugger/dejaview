# DejaView VS Code Extension

The DejaView VS Code extension adds a VS Code debug adapter for the DejaView Python debugger.

It contributes a debugger type named `dejaview-python-pdb` (shown as **DejaView Python (pdb)**) and launches your target script through DejaView so you can use standard debugging controls, including reverse-stepping support exposed by the adapter.

For core debugger behavior, reverse commands, and CLI details, see the main project: [https://github.com/dejaview-debugger/dejaview](https://github.com/dejaview-debugger/dejaview).

## Prerequisites

Before using the extension, make sure you have:

- VS Code 1.95.0 or newer
- Python 3.12
- DejaView installed in the environment you want to debug

Install DejaView from source:

```bash
pip install "dejaview @ git+https://github.com/dejaview-debugger/dejaview"
```

Or with uv:

```bash
uv pip install "dejaview @ git+https://github.com/dejaview-debugger/dejaview"
```

## Using the Extension

1. Open your Python workspace in VS Code.
2. Create or edit `.vscode/launch.json`.
3. Add a debug configuration with `type: "dejaview-python-pdb"`.
4. Start debugging from the Run and Debug view.

Example `launch.json` configuration:

```json
{
	"version": "0.2.0",
	"configurations": [
		{
			"name": "DejaView: Debug current file",
			"type": "dejaview-python-pdb",
			"request": "launch",
			"program": "${file}",
			"pythonPath": "python3",
			"cwd": "${workspaceFolder}",
			"port": 5678
		}
	]
}
```

### Launch Configuration Reference

- `program` (required): Path to the Python script to debug.
- `pythonPath` (optional): Python executable to run. Default is `python3`.
- `cwd` (optional): Working directory for the debugged process.
- `port` (optional): Local TCP port for adapter/debugger communication. Default is `5678`.

## Development

From this folder, install dependencies and compile:

```bash
npm install
npm run compile
```

To watch TypeScript changes:

```bash
npm run watch
```

To run the extension in an Extension Development Host:

1. Open this folder in VS Code.
2. Press `F5`.
3. In the new VS Code window, run a `python-pdb` launch configuration.

## Testing

Run the extension test suite:

```bash
npm test
```

This compiles the extension, runs linting, and executes tests in `src/test/extension.test.ts`.

## Notes and Limitations

- This extension is a debug adapter wrapper; debugger capabilities come from the DejaView Python package.
- If reverse execution does not behave as expected, check the DejaView limitations in the main README.
- Choose a unique `port` if you run multiple debug sessions concurrently.

## References

- Main debugger docs: [https://github.com/dejaview-debugger/dejaview](https://github.com/dejaview-debugger/dejaview)
