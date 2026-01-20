import * as vscode from "vscode";
import * as net from "net";

import { ContinuedEvent, DebugSession, LoggingDebugSession, InitializedEvent, TerminatedEvent, OutputEvent, StoppedEvent, Variable, StackFrame, ThreadEvent, Thread } from "@vscode/debugadapter";
import { DebugProtocol } from "@vscode/debugprotocol";

// Extend the DebugProtocol.LaunchRequestArguments type
interface PythonLaunchRequestArguments extends DebugProtocol.LaunchRequestArguments {
  program: string; // Add the 'program' property
  pythonPath?: string; // Optional Python binary path
  cwd?: string; // Optional working directory
  port?: number; // Optional debug port (default: 5678)
}

export class PythonDebugAdapter extends LoggingDebugSession {
  // Set to false to disable debug logs
  private static readonly DEBUG = false;

  private process: import("child_process").ChildProcess | null = null;
  private breakpoints: { [file: string]: number[] } = {}; // Store breakpoints by file
  private is_running: boolean = false;
  private socket: net.Socket | null = null;
  private server: net.Server | null = null;
  private port: number = 5678;
  private messageBuffer: string = "";
  private commandQueue: Array<{ command: string; callback?: (data: any) => void }> = [];
  private currentStopLocation: { filename: string; lineno: number } | null = null;

  constructor() {
    super();
    this.debugLog("PythonDebugAdapter constructed");
  }

  private debugLog(message: string): void {
    if (PythonDebugAdapter.DEBUG) {
      this.sendEvent(new OutputEvent(message + "\n", "console"));
    }
  }

  protected initializeRequest(response: DebugProtocol.InitializeResponse, args: DebugProtocol.InitializeRequestArguments): void {
    this.debugLog("Initialize request received");
    response.body = response.body || {};
    response.body.supportsConfigurationDoneRequest = true;
    response.body.supportsStepBack = true;
    response.body.supportsStepInTargetsRequest = true;
    response.body.supportsSteppingGranularity = true;
    response.body.supportsSingleThreadExecutionRequests = true;
    // response.body.supportsSetVariable = true;
    this.sendResponse(response);
  }

  protected configurationDoneRequest(response: DebugProtocol.ConfigurationDoneResponse, args: DebugProtocol.ConfigurationDoneArguments, request?: DebugProtocol.Request): void {
    super.configurationDoneRequest(response, args);
    // Get to the first breakpoint
    this.sendCommand("continue");
    this.sendEvent(new ContinuedEvent(1));
    this.sendEvent(new StoppedEvent("breakpoint", 1));
    this.sendResponse(response);
  }

  protected setBreakPointsRequest(response: DebugProtocol.SetBreakpointsResponse, args: DebugProtocol.SetBreakpointsArguments): void {
    const filePath = args.source.path!;
    const breakpoints = args.breakpoints || [];
    const breakpoint_lines = breakpoints.map((bp) => bp.line);

    // Set new breakpoints
    breakpoint_lines.forEach((line) => {
      const breakCommand = `break ${filePath}:${line}`;
      this.debugLog(`Setting breakpoint: ${breakCommand}`);
      this.sendCommand(breakCommand);
    });

    // Update internal breakpoint state
    this.breakpoints[filePath] = breakpoint_lines;

    // Respond to VSCode with the updated breakpoints
    response.body = {
      breakpoints: breakpoints.map((bp) => ({
        verified: true, // Mark as verified since pdb doesn't verify programmatically
        line: bp.line,
      })),
    };
    this.sendResponse(response);
  }

  protected launchRequest(response: DebugProtocol.LaunchResponse, args: PythonLaunchRequestArguments): void {
    // Start a subprocess for pdb (ensure args.program is the script)
    this.debugLog("=== LAUNCH REQUEST STARTED ===");
    this.debugLog(`Program: ${args.program}`);
    this.debugLog(`Python: ${args.pythonPath}`);
    this.debugLog(`CWD: ${args.cwd}`);

    this.port = args.port || 5678;
    this.debugLog(`Port: ${this.port}`);

    // Create TCP server to listen for debugger connection
    this.server = net.createServer((socket) => {
      this.debugLog("Debugger connected via socket");
      this.socket = socket;

      socket.on("data", (data: Buffer) => {
        this.handleSocketData(data);
      });

      socket.on("error", (err) => {
        this.sendEvent(new OutputEvent(`Socket error: ${err.message}\n`, "stderr"));
      });

      socket.on("close", () => {
        this.debugLog("Socket closed");
        this.sendEvent(new TerminatedEvent());
      });

      // Flush any queued commands now that socket is connected
      this.flushCommandQueue();
    });

    this.server.listen(this.port, "127.0.0.1", () => {
      this.debugLog(`Server listening on port ${this.port}`);

      // Now spawn the Python process with the port as an argument
      const { spawn } = require("child_process");
      const pythonPath = args.pythonPath || "python3";
      const workingDir = args.cwd;
      const process = spawn(pythonPath, ["-m", "dejaview", "--port", this.port.toString(), args.program], { cwd: workingDir });

      if (process !== null) {
        process.stdout.on("data", (data: Buffer) => {
          const output = data.toString();
          this.sendEvent(new OutputEvent(output, "stdout"));
        });

        process.stderr.on("data", (data: Buffer) => {
          this.sendEvent(new OutputEvent(data.toString(), "stderr"));
        });

        process.on("close", (code: number) => {
          this.sendEvent(new OutputEvent(`Process exited with code ${code}\n`, "console"));
          if (this.socket) {
            this.socket.end();
          }
          if (this.server) {
            this.server.close();
          }
        });
        this.process = process;
      }
    });

    this.sendResponse(response);
    this.sendEvent(new InitializedEvent());
  }

  private handleSocketData(data: Buffer): void {
    // Accumulate data in buffer
    this.messageBuffer += data.toString();

    // Process complete messages (delimited by newlines)
    let newlineIndex;
    while ((newlineIndex = this.messageBuffer.indexOf("\n")) !== -1) {
      const message = this.messageBuffer.substring(0, newlineIndex);
      this.messageBuffer = this.messageBuffer.substring(newlineIndex + 1);

      if (message.trim()) {
        this.debugLog(`Received message: ${message}`);
        this.processDebuggerMessage(message);
      }
    }
  }

  private processDebuggerMessage(message: string): void {
    try {
      const data = JSON.parse(message);

      switch (data.type) {
        case "output":
          this.sendEvent(new OutputEvent(data.content, data.category || "console"));
          break;
        case "stopped":
          this.is_running = false;
          // Cache location if provided in the stopped event
          if (data.filename && data.lineno) {
            this.currentStopLocation = { filename: data.filename, lineno: data.lineno };
            this.debugLog(`Cached stop location: ${data.filename}:${data.lineno}`);
          } else {
            this.currentStopLocation = null;
          }
          this.sendEvent(new StoppedEvent(data.reason || "step", data.threadId || 1));
          break;
        case "continued":
          this.is_running = true;
          this.currentStopLocation = null;
          this.sendEvent(new ContinuedEvent(data.threadId || 1));
          break;
        case "terminated":
          this.sendEvent(new TerminatedEvent());
          break;
        case "response":
          // Handle responses to commands (e.g., for variables, stack trace)
          this.handleCommandResponse(data);
          break;
        default:
          throw new Error(`Unknown message type: ${data.type}`);
      }
    } catch (e) {
      this.sendEvent(new OutputEvent(`Error processing message: ${e}\n`, "stderr"));
      throw e;
    }
  }

  private commandCallbacks: Map<string, (data: any) => void> = new Map();

  private handleCommandResponse(data: any): void {
    const callback = this.commandCallbacks.get(data.command);
    if (callback) {
      callback(data);
      this.commandCallbacks.delete(data.command);
    }
  }

  private sendCommand(command: string, callback?: (data: any) => void): void {
    if (this.socket && !this.socket.destroyed) {
      const message = JSON.stringify({ command }) + "\n";
      this.socket.write(message);

      if (callback) {
        this.commandCallbacks.set(command, callback);
      }
    } else {
      // Queue the command if socket is not connected yet
      this.commandQueue.push({ command, callback });
    }
  }

  private flushCommandQueue(): void {
    this.debugLog(`Flushing ${this.commandQueue.length} queued commands`);
    while (this.commandQueue.length > 0) {
      const { command, callback } = this.commandQueue.shift()!;
      this.sendCommand(command, callback);
    }
  }

  // Handle input from the Debug Console
  protected evaluateRequest(response: DebugProtocol.EvaluateResponse, args: DebugProtocol.EvaluateArguments): void {
    this.sendCommand(args.expression);

    response.body = {
      result: "",
      variablesReference: 0,
    };
    this.sendResponse(response);
  }

  protected continueRequest(response: DebugProtocol.ContinueResponse, args: DebugProtocol.ContinueArguments): void {
    this.is_running = true;
    this.sendCommand("continue");
    this.sendEvent(new ContinuedEvent(args.threadId || 1));
    this.sendResponse(response);
  }

  protected nextRequest(response: DebugProtocol.NextResponse, args: DebugProtocol.NextArguments, request?: DebugProtocol.Request): void {
    this.is_running = true;
    this.sendCommand("next");
    this.sendEvent(new ContinuedEvent(args.threadId || 1));
    this.sendResponse(response);
  }

  protected stepInRequest(response: DebugProtocol.StepInResponse, args: DebugProtocol.StepInArguments, request?: DebugProtocol.Request): void {
    this.is_running = true;
    this.sendCommand("step");
    this.sendEvent(new ContinuedEvent(args.threadId || 1));
    this.sendResponse(response);
  }

  protected stepOutRequest(response: DebugProtocol.StepOutResponse, args: DebugProtocol.StepOutArguments): void {
    this.is_running = true;
    this.sendCommand("return");
    this.sendEvent(new ContinuedEvent(args.threadId || 1));
    this.sendResponse(response);
  }

  protected stepBackRequest(response: DebugProtocol.StepBackResponse, args: DebugProtocol.StepBackArguments): void {
    this.is_running = true;
    this.sendCommand("back");
    this.sendEvent(new ContinuedEvent(args.threadId || 1));
    this.sendResponse(response);
  }

  protected reverseContinueRequest(response: DebugProtocol.StepBackResponse, args: DebugProtocol.StepBackArguments): void {
    this.is_running = true;
    this.sendCommand("rc");
    this.sendEvent(new ContinuedEvent(args.threadId || 1));
    this.sendResponse(response);
  }

  protected pauseRequest(response: DebugProtocol.PauseResponse, args: DebugProtocol.PauseArguments, request?: DebugProtocol.Request): void {
    this.sendResponse(response);
  }

  protected variablesRequest(response: DebugProtocol.VariablesResponse, args: DebugProtocol.VariablesArguments): void {
    if (args.variablesReference === 1) {
      // Local variables - use socket communication
      this.sendCommand("locals()", (data) => {
        const variables: DebugProtocol.Variable[] = [];
        const varsData = data.variables || {};

        for (const [key, value] of Object.entries(varsData)) {
          variables.push({
            name: key,
            value: String(value),
            type: typeof value,
            variablesReference: 0,
          });
        }

        response.body = { variables };
        this.sendResponse(response);
      });
    } else {
      response.body = { variables: [] };
      this.sendResponse(response);
    }
  }

  private parseVariables(output: string): Record<string, any> {
    try {
      // Remove any extraneous text and evaluate the dictionary
      const localsStart = output.indexOf("{");
      const localsEnd = output.lastIndexOf("}");
      if (localsStart !== -1 && localsEnd !== -1) {
        const localsString = output.substring(localsStart, localsEnd + 1);
        return JSON.parse(
          localsString.replace(/'/g, '"') // Convert single quotes to double quotes for JSON parsing
        );
      }
    } catch (e) {
      console.error("Failed to parse variables:", e);
    }
    return {};
  }

  protected scopesRequest(response: DebugProtocol.ScopesResponse, args: DebugProtocol.ScopesArguments): void {
    const scopes = [
      {
        name: "Local",
        variablesReference: 1, // Reference ID for local variables
        expensive: false,
      },
      /*{
        name: "Global",
        variablesReference: 2, // Reference ID for global variables
        expensive: true,
      },*/
    ];
    response.body = { scopes };
    this.sendResponse(response);
  }

  private parseCallStack(output: string): DebugProtocol.StackFrame[] {
    const stackFrames: DebugProtocol.StackFrame[] = [];
    const lines = output.split("\n");

    lines.reverse().forEach((line, index) => {
      const match = line.match(/^>?\s+(.*)\((\d+)\)(.*)$/);
      if (match) {
        let [, file, lineNumber, functionName] = match;
        // Blacklist pdb, bdb
        if (file.includes("pdb") || file.includes("bdb")) {
          return;
        }
        // Blacklist files with '<' and '>'
        if (file.includes("<") || file.includes(">")) {
          return;
        }
        functionName = functionName.substring(0, functionName.length - 2);
        stackFrames.push({
          id: stackFrames.length, // Unique frame ID
          name: functionName,
          source: {
            path: file,
            name: file.split("/").pop(),
          },
          line: parseInt(lineNumber, 10),
          column: 0, // Columns are optional
        });
      }
    });

    if (stackFrames.length === 0) {
      stackFrames.push({
        id: 1,
        name: "<no stack>",
        source: {
          path: "<unknown>",
          name: "<unknown>",
        },
        line: 0,
        column: 0,
      });
    }

    return stackFrames;
  }

  protected stackTraceRequest(response: DebugProtocol.StackTraceResponse, args: DebugProtocol.StackTraceArguments, request?: DebugProtocol.Request): void {
    // If we have a cached stop location, use it for the top frame
    if (this.currentStopLocation) {
      this.debugLog(`Using cached stop location: ${this.currentStopLocation.filename}:${this.currentStopLocation.lineno}`);

      // Send the `where` command via socket to get the full stack
      this.sendCommand("where", (data) => {
        const frames = data.stackFrames || [];

        // Override the top frame with the cached location
        if (frames.length > 0) {
          frames[0].line = this.currentStopLocation!.lineno;
          frames[0].source = {
            path: this.currentStopLocation!.filename,
            name: this.currentStopLocation!.filename.split("/").pop() || this.currentStopLocation!.filename,
          };
          this.debugLog(`Overrode top frame to ${this.currentStopLocation!.filename}:${this.currentStopLocation!.lineno}`);
        }

        response.body = {
          stackFrames: frames,
          totalFrames: frames.length,
        };
        this.sendResponse(response);
      });
    } else {
      // No cached location, use the `where` command normally
      this.sendCommand("where", (data) => {
        const frames = data.stackFrames || [];

        response.body = {
          stackFrames: frames,
          totalFrames: frames.length,
        };
        this.sendResponse(response);
      });
    }
  }

  protected threadsRequest(response: DebugProtocol.ThreadsResponse, request?: DebugProtocol.Request): void {
    response.body = {
      threads: [new Thread(1, "Main Thread")],
    };
    this.sendResponse(response);
  }

  protected disconnectRequest(response: DebugProtocol.DisconnectResponse, args: DebugProtocol.DisconnectArguments): void {
    if (this.socket) {
      this.socket.end();
    }
    if (this.server) {
      this.server.close();
    }
    if (this.process) {
      this.process.kill();
    }
    this.sendResponse(response);
  }
}

DebugSession.run(PythonDebugAdapter);
