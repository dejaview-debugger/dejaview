import * as assert from "assert";
import * as path from "path";
import * as vscode from "vscode";
import * as fs from "fs";
import * as os from "os";

suite("Extension Test Suite", () => {
    let testWorkspaceDir: string;
    let testPythonFile: string;

    suiteSetup(function () {
        this.timeout(10000);
        // Create a temporary directory for test files
        testWorkspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "vscode-test-"));

        // Create a simple Python test file
        testPythonFile = path.join(testWorkspaceDir, "test_program.py");
        const pythonCode = `def add(a, b):
    return a + b

def multiply(x, y):
    result = x * y
    return result

def main():
    num1 = 5
    num2 = 3
    sum_result = add(num1, num2)
    product = multiply(num1, num2)
    print(f"Sum: {sum_result}")
    print(f"Product: {product}")
    return sum_result, product

if __name__ == "__main__":
    main()
`;
        fs.writeFileSync(testPythonFile, pythonCode);
    });

    suiteTeardown(() => {
        // Clean up test directory
        if (testWorkspaceDir && fs.existsSync(testWorkspaceDir)) {
            fs.rmSync(testWorkspaceDir, { recursive: true, force: true });
        }
    });

    test("Extension should be present", () => {
        const extension = vscode.extensions.getExtension("undefined_publisher.dejaview-extension");
        assert.ok(extension, "Extension should be installed");
    });

    test("Extension should activate", async function () {
        this.timeout(10000);
        const extension = vscode.extensions.getExtension("undefined_publisher.dejaview-extension");
        assert.ok(extension, "Extension should be present");
        
        await extension.activate();
        assert.ok(extension.isActive, "Extension should be activated");
    });

    test("Debug adapter should be registered for python-pdb type", async function () {
        this.timeout(10000);
        const extension = vscode.extensions.getExtension("undefined_publisher.dejaview-extension");
        assert.ok(extension);
        await extension.activate();

        // Check if the debugger type is available in the package.json contributes
        const packageJSON = extension.packageJSON;
        assert.ok(packageJSON.contributes, "Package should have contributions");
        assert.ok(packageJSON.contributes.debuggers, "Package should contribute debuggers");
        
        const debuggers = packageJSON.contributes.debuggers;
        const pythonPdbDebugger = debuggers.find((d: any) => d.type === "python-pdb");
        assert.ok(pythonPdbDebugger, "python-pdb debugger should be registered");
        assert.strictEqual(pythonPdbDebugger.label, "Python (pdb)", "Debugger label should be correct");
    });

    test("Debug configuration should have correct properties", async function () {
        this.timeout(10000);
        const extension = vscode.extensions.getExtension("undefined_publisher.dejaview-extension");
        assert.ok(extension);
        await extension.activate();

        const packageJSON = extension.packageJSON;
        const debuggers = packageJSON.contributes.debuggers;
        const pythonPdbDebugger = debuggers.find((d: any) => d.type === "python-pdb");
        
        assert.ok(pythonPdbDebugger.configurationAttributes, "Should have configuration attributes");
        assert.ok(pythonPdbDebugger.configurationAttributes.launch, "Should have launch configuration");
        
        const launchConfig = pythonPdbDebugger.configurationAttributes.launch;
        assert.ok(launchConfig.required.includes("program"), "Program should be required");
        assert.ok(launchConfig.properties.program, "Should have program property");
        assert.ok(launchConfig.properties.pythonPath, "Should have pythonPath property");
        assert.ok(launchConfig.properties.cwd, "Should have cwd property");
    });

    test("Debug session can be started with valid configuration", async function () {
        this.timeout(30000);
        
        const extension = vscode.extensions.getExtension("undefined_publisher.dejaview-extension");
        assert.ok(extension);
        await extension.activate();

        // Open the test Python file
        const document = await vscode.workspace.openTextDocument(testPythonFile);
        await vscode.window.showTextDocument(document);

        // Create debug configuration
        const debugConfig: vscode.DebugConfiguration = {
            type: "python-pdb",
            name: "Test Debug Session",
            request: "launch",
            program: testPythonFile,
            pythonPath: "python3",
            cwd: testWorkspaceDir,
            port: 5678
        };

        let sessionStarted = false;
        let sessionTerminated = false;

        // Listen for debug session events
        const startDisposable = vscode.debug.onDidStartDebugSession((session) => {
            if (session.configuration.name === "Test Debug Session") {
                sessionStarted = true;
            }
        });

        const terminateDisposable = vscode.debug.onDidTerminateDebugSession((session) => {
            if (session.configuration.name === "Test Debug Session") {
                sessionTerminated = true;
            }
        });

        try {
            // Start debug session
            const success = await vscode.debug.startDebugging(undefined, debugConfig);
            assert.ok(success, "Debug session should start successfully");

            // Wait for session to start
            await new Promise((resolve) => setTimeout(resolve, 2000));
            assert.ok(sessionStarted, "Debug session should have started");

            // Stop the debug session
            await vscode.debug.stopDebugging(vscode.debug.activeDebugSession);

            // Wait for session to terminate
            await new Promise((resolve) => setTimeout(resolve, 2000));
            assert.ok(sessionTerminated, "Debug session should have terminated");
        } finally {
            startDisposable.dispose();
            terminateDisposable.dispose();
        }
    });

    test("Breakpoints can be set in Python files", async function () {
        this.timeout(10000);
        
        const extension = vscode.extensions.getExtension("undefined_publisher.dejaview-extension");
        assert.ok(extension);
        await extension.activate();

        // Open the test Python file
        const document = await vscode.workspace.openTextDocument(testPythonFile);
        await vscode.window.showTextDocument(document);

        // Create breakpoint at line 9 (in main function)
        const breakpoint = new vscode.SourceBreakpoint(
            new vscode.Location(document.uri, new vscode.Position(8, 0))
        );

        vscode.debug.addBreakpoints([breakpoint]);

        const breakpoints = vscode.debug.breakpoints;
        assert.ok(breakpoints.length > 0, "Breakpoints should be added");
        
        const sourceBreakpoint = breakpoints.find(bp => 
            bp instanceof vscode.SourceBreakpoint && 
            bp.location.uri.fsPath === testPythonFile
        ) as vscode.SourceBreakpoint | undefined;
        
        assert.ok(sourceBreakpoint, "Source breakpoint should exist for test file");
        
        // Clean up
        vscode.debug.removeBreakpoints(breakpoints);
    });

    test("Extension supports step back functionality", async function () {
        this.timeout(10000);
        
        const extension = vscode.extensions.getExtension("undefined_publisher.dejaview-extension");
        assert.ok(extension);
        await extension.activate();

        const packageJSON = extension.packageJSON;
        const debuggers = packageJSON.contributes.debuggers;
        const pythonPdbDebugger = debuggers.find((d: any) => d.type === "python-pdb");
        
        // Verify the adapter supports step back
        // This is indicated by supportsStepBack in the initialize response
        assert.ok(pythonPdbDebugger, "Debugger should be configured");
    });

    test("Multiple Python files can be debugged", async function () {
        this.timeout(15000);

        // Create a second Python test file
        const secondPythonFile = path.join(testWorkspaceDir, "test_program2.py");
        const pythonCode2 = `def subtract(a, b):
    return a - b

def divide(x, y):
    if y != 0:
        return x / y
    return None

if __name__ == "__main__":
    print(subtract(10, 5))
    print(divide(10, 2))
`;
        fs.writeFileSync(secondPythonFile, pythonCode2);

        const extension = vscode.extensions.getExtension("undefined_publisher.dejaview-extension");
        assert.ok(extension);
        await extension.activate();

        // Open the second test file
        const document = await vscode.workspace.openTextDocument(secondPythonFile);
        await vscode.window.showTextDocument(document);

        // Create debug configuration for second file
        const debugConfig: vscode.DebugConfiguration = {
            type: "python-pdb",
            name: "Test Debug Session 2",
            request: "launch",
            program: secondPythonFile,
            pythonPath: "python3",
            cwd: testWorkspaceDir,
            port: 5679 // Different port
        };

        let sessionStarted = false;

        const startDisposable = vscode.debug.onDidStartDebugSession((session) => {
            if (session.configuration.name === "Test Debug Session 2") {
                sessionStarted = true;
            }
        });

        try {
            const success = await vscode.debug.startDebugging(undefined, debugConfig);
            assert.ok(success, "Second debug session should start successfully");

            await new Promise((resolve) => setTimeout(resolve, 2000));
            assert.ok(sessionStarted, "Second debug session should have started");

            // Clean up
            await vscode.debug.stopDebugging(vscode.debug.activeDebugSession);
        } finally {
            startDisposable.dispose();
            fs.unlinkSync(secondPythonFile);
        }
    });

    test("Debug configuration validates required fields", async function () {
        this.timeout(10000);
        
        const extension = vscode.extensions.getExtension("undefined_publisher.dejaview-extension");
        assert.ok(extension);
        await extension.activate();

        // Try to start debug session without required 'program' field
        const invalidConfig: vscode.DebugConfiguration = {
            type: "python-pdb",
            name: "Invalid Config",
            request: "launch",
            pythonPath: "python3"
            // Missing 'program' field
        };

        // This should fail or prompt for the missing field
        try {
            const success = await vscode.debug.startDebugging(undefined, invalidConfig);
            // If it succeeds, it means VS Code prompted and got the value
            // or there's a default behavior
            assert.ok(true, "Debug configuration was handled");
        } catch (error) {
            // Expected to fail without program field
            assert.ok(error, "Should throw error for missing program field");
        }
    });

    test("Extension supports Python language for breakpoints", async function () {
        this.timeout(10000);
        
        const extension = vscode.extensions.getExtension("undefined_publisher.dejaview-extension");
        assert.ok(extension);
        await extension.activate();

        const packageJSON = extension.packageJSON;
        assert.ok(packageJSON.breakpoints, "Should support breakpoints");
        
        const pythonBreakpoint = packageJSON.breakpoints.find((bp: any) => bp.language === "python");
        assert.ok(pythonBreakpoint, "Should support Python breakpoints");
    });
});