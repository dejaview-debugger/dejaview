// The module 'vscode' contains the VS Code extensibility API
// Import the module and reference it with the alias vscode in your code below
import * as vscode from "vscode";
import { PythonDebugAdapter } from "./adapter";
import { DebugSession } from "@vscode/debugadapter";

import * as path from "path";
class PythonDebugAdapterFactory implements vscode.DebugAdapterDescriptorFactory {
  constructor(private context: vscode.ExtensionContext) {}

  createDebugAdapterDescriptor(session: vscode.DebugSession): vscode.ProviderResult<vscode.DebugAdapterDescriptor> {
    const adapterPath = path.join(this.context.extensionPath, "out", "adapter.js");
    return new vscode.DebugAdapterExecutable("node", [adapterPath]);
  }
}

// This method is called when the extension is activated
export function activate(context: vscode.ExtensionContext) {
  // Attach the debug adapter factory to the "dejaview-python-pdb" debug type
  context.subscriptions.push(vscode.debug.registerDebugAdapterDescriptorFactory("dejaview-python-pdb", new PythonDebugAdapterFactory(context)));
}

// This method is called when the extension is deactivated
export function deactivate() {}
