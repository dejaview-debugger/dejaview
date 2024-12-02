import * as vscode from "vscode";

import {
  ContinuedEvent,
  DebugSession,
  LoggingDebugSession,
  InitializedEvent,
  TerminatedEvent,
  OutputEvent,
  StoppedEvent,
  Variable,
  StackFrame,
  ThreadEvent,
  Thread,
} from "@vscode/debugadapter";
import { DebugProtocol } from "@vscode/debugprotocol";

// Extend the DebugProtocol.LaunchRequestArguments type
interface PythonLaunchRequestArguments extends DebugProtocol.LaunchRequestArguments {
  program: string; // Add the 'program' property
}

export class PythonDebugAdapter extends LoggingDebugSession {
  private process: import("child_process").ChildProcess | null = null;
  private breakpoints: { [file: string]: number[] } = {}; // Store breakpoints by file
  private is_running: boolean = false;

  constructor() {
    super();
  }

  protected initializeRequest(
    response: DebugProtocol.InitializeResponse,
    args: DebugProtocol.InitializeRequestArguments
  ): void {
    response.body = response.body || {};
    response.body.supportsConfigurationDoneRequest = true;
    response.body.supportsStepBack = true;
    response.body.supportsStepInTargetsRequest = true;
    response.body.supportsSteppingGranularity = true;
    response.body.supportsSingleThreadExecutionRequests = true;
    this.sendResponse(response);
  }

  protected configurationDoneRequest(
    response: DebugProtocol.ConfigurationDoneResponse,
    args: DebugProtocol.ConfigurationDoneArguments,
    request?: DebugProtocol.Request
  ): void {
    super.configurationDoneRequest(response, args);
    // Get to the first breakpoint
    this.process?.stdin?.write("continue\n");
    this.sendEvent(new ContinuedEvent(1));
    this.sendEvent(new StoppedEvent("breakpoint", 1));
    this.sendResponse(response);
  }

  protected setBreakPointsRequest(
    response: DebugProtocol.SetBreakpointsResponse,
    args: DebugProtocol.SetBreakpointsArguments
  ): void {
    const filePath = args.source.path!;
    const breakpoints = args.breakpoints || [];
    const breakpoint_lines = breakpoints.map((bp) => bp.line);

    // Set new breakpoints
    breakpoint_lines.forEach((line) => {
      const breakCommand = `break ${filePath}:${line}\n`;
      this.process?.stdin?.write(breakCommand);
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

  protected launchRequest(
    response: DebugProtocol.LaunchResponse,
    args: PythonLaunchRequestArguments
  ): void {
    /// this.sendEvent(new OutputEvent(`Launching program: ${args.program}\n`));
    // Start a subprocess for pdb (ensure args.program is the script)
    const { spawn } = require("child_process");
    const process = spawn("python", ["-m", "pdb", args.program]);

    if (process !== null) {
      process.stdout.on("data", (data: Buffer) => {
        const output = data.toString();
        /// this.sendEvent(new OutputEvent("DEBUG: ----------------\n"));
        this.sendEvent(new OutputEvent(output));

        // Detect if execution is paused and notify VSCode
        if (output.includes("->") && this.is_running) {
          // Example marker for a paused state
          /// this.sendEvent(new OutputEvent("DEBUG: PAUSED"));
          this.is_running = false;
          this.sendEvent(new StoppedEvent("step", 1)); // Notify paused state
        }

        // Detect if a breakpoint has been hit
        /*if (output.includes("Breakpoint")) {
          const match = output.match(/at (.*):(\d+)/);
          if (match) {
            const [, filePath, line] = match;
            this.sendEvent(new StoppedEvent("breakpoint", 1));

            // Optionally, log breakpoint hit information
            /// console.log(`Breakpoint hit at ${filePath}:${line}`);
          }
        }*/
      });

      process.stderr.on("data", (data: Buffer) => {
        this.sendEvent(new OutputEvent(data.toString(), "stderr"));
      });

      process.on("close", () => {
        this.sendEvent(new TerminatedEvent());
      });
      this.process = process;
    }
    this.sendResponse(response);
    this.sendEvent(new InitializedEvent());
  }

  // Handle input from the Debug Console
  protected evaluateRequest(
    response: DebugProtocol.EvaluateResponse,
    args: DebugProtocol.EvaluateArguments
  ): void {
    if (this.process !== null && this.process.stdin !== null) {
      const expression = args.expression + "\n"; // Append newline for pdb
      this.process.stdin.write(expression);
    }

    response.body = {
      result: "",
      variablesReference: 0,
    };
    this.sendResponse(response);
  }

  protected continueRequest(
    response: DebugProtocol.ContinueResponse,
    args: DebugProtocol.ContinueArguments
  ): void {
    if (this.process) {
      this.is_running = true;
      this.process?.stdin?.write("continue\n");
      this.sendEvent(new ContinuedEvent(1)); // Notify that the program has resumed
    }
    this.sendResponse(response); // Notify VSCode that the command was sent
  }

  protected nextRequest(
    response: DebugProtocol.NextResponse,
    args: DebugProtocol.NextArguments,
    request?: DebugProtocol.Request
  ): void {
    if (this.process) {
      this.is_running = true;
      this.process?.stdin?.write("next\n");
      this.sendEvent(new ContinuedEvent(1)); // Notify that the program has resumed
    }
    this.sendResponse(response);
  }

  protected stepInRequest(
    response: DebugProtocol.StepInResponse,
    args: DebugProtocol.StepInArguments,
    request?: DebugProtocol.Request
  ): void {
    if (this.process) {
      this.is_running = true;
      this.process?.stdin?.write("step\n");
      this.sendEvent(new ContinuedEvent(1)); // Notify that the program has resumed
    }
    this.sendResponse(response);
  }

  protected stepOutRequest(
    response: DebugProtocol.StepOutResponse,
    args: DebugProtocol.StepOutArguments
  ): void {
    if (this.process) {
      this.is_running = true;
      this.process?.stdin?.write("return\n");
      this.sendEvent(new ContinuedEvent(1)); // Notify that the program has resumed
    }
    this.sendResponse(response);
  }

  protected stepBackRequest(
    response: DebugProtocol.StepOutResponse,
    args: DebugProtocol.StepOutArguments
  ): void {
    if (this.process) {
      this.process?.stdin?.write("back\n");
    }
    this.sendResponse(response);
  }

  protected pauseRequest(
    response: DebugProtocol.PauseResponse,
    args: DebugProtocol.PauseArguments,
    request?: DebugProtocol.Request
  ): void {
    /// this.sendEvent(new OutputEvent("DEBUG: PAUSE REQUESTED"));
    this.sendResponse(response);
  }

  protected variablesRequest(
    response: DebugProtocol.VariablesResponse,
    args: DebugProtocol.VariablesArguments
  ): void {
    const variables: DebugProtocol.Variable[] = [];
    /// this.sendEvent(new OutputEvent("DEBUG: VARIABLES REQUESTED\n"));

    if (args.variablesReference === 1) {
      // Local variables
      this.process?.stdin?.write("locals()\n");
    }
    /* else if (args.variablesReference === 2) {
      // Global variables
      this.process?.stdin?.write("globals()\n");
    }*/

    this.process?.stdout?.once("data", (data: Buffer) => {
      const output = data.toString();
      const parsedVariables = this.parseVariables(output);

      for (const [key, value] of Object.entries(parsedVariables)) {
        variables.push({
          name: key,
          value: value.toString(),
          type: typeof value,
          variablesReference: 0, // Make this non-zero for expandable variables
        });
        /// this.sendEvent(new OutputEvent(`DEBUG: ${key} = ${value}\n`));
      }

      response.body = { variables };
      this.sendResponse(response);
    });
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

  protected scopesRequest(
    response: DebugProtocol.ScopesResponse,
    args: DebugProtocol.ScopesArguments
  ): void {
    /// this.sendEvent(new OutputEvent("DEBUG: SCOPES REQUESTED\n"));
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

    lines.forEach((line, index) => {
      const match = line.match(/(.*)\((\d+)\)(.*)/);
      if (match) {
        let [, file, lineNumber, functionName] = match;
        file = file.substring(2, file.length);
        // Blacklist pdb, bdb
        if (file.includes("pdb") || file.includes("bdb")) {
          return;
        }
        // Blacklist functions with '<' and '>'
        if (functionName.includes("<") || functionName.includes(">")) {
          return;
        }
        functionName = functionName.substring(0, functionName.length - 2);
        /// this.sendEvent(new OutputEvent(`DEBUG???: ${file}:${lineNumber} ${functionName}\n`));
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

  protected stackTraceRequest(
    response: DebugProtocol.StackTraceResponse,
    args: DebugProtocol.StackTraceArguments,
    request?: DebugProtocol.Request
  ): void {
    // Send the `where` command to pdb
    /// this.sendEvent(new OutputEvent("DEBUG: STACK TRACE REQUESTED\n"));
    this.process?.stdin?.write("where\n");

    this.process?.stdout?.once("data", (data: Buffer) => {
      const output = data.toString();

      // Parse pdb output into stack frames
      const frames = this.parseCallStack(output);

      response.body = {
        stackFrames: frames,
        totalFrames: frames.length,
      };
      this.sendResponse(response);
    });
  }

  protected threadsRequest(
    response: DebugProtocol.ThreadsResponse,
    request?: DebugProtocol.Request
  ): void {
    response.body = {
      threads: [new Thread(1, "Main Thread")],
    };
    this.sendResponse(response);
  }

  protected disconnectRequest(
    response: DebugProtocol.DisconnectResponse,
    args: DebugProtocol.DisconnectArguments
  ): void {
    if (this.process) {
      this.process.kill();
    }
    this.sendResponse(response);
  }
}

DebugSession.run(PythonDebugAdapter);
