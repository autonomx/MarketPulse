// Agent sandbox relay (WP-4.7). The one container on the internal sandbox
// network with a route out. Each "port=host:port" argument listens on port and
// forwards HTTP requests to that one gateway target, but only requests whose
// path is an agent-runtime route ("--allow=<regex>", matched against the
// percent-decoded path, the form the gateway routes on). Every other request,
// and every protocol upgrade, is refused here, so a sandboxed agent can reach
// its broker and model gateway and nothing else on the gateway, whether or not
// sign-in is enforced. Forwarded requests carry x-omnix-sandbox-relay, which
// the gateway also checks.
//
// "--tcp" instead forwards raw bytes with no filtering. It is only for the
// workspace preview's ingress (host loopback -> the preview's dev server,
// including its websocket reload), which is not the sandbox's way out.
import http from 'node:http';
import net from 'node:net';

export const RELAY_HEADER = 'x-omnix-sandbox-relay';

export function parseArguments(argv) {
  const routes = [];
  const allowed = [];
  let tcp = false;
  for (const spec of argv) {
    if (spec === '--tcp') {
      tcp = true;
      continue;
    }
    if (spec.startsWith('--allow=')) {
      allowed.push(new RegExp(spec.slice('--allow='.length)));
      continue;
    }
    const match = /^(\d+)=([^:]+):(\d+)$/.exec(spec);
    if (!match) {
      throw new Error(`invalid route ${spec}; expected port=host:port or --allow=<regex>`);
    }
    routes.push({ listen: Number(match[1]), host: match[2], port: Number(match[3]) });
  }
  if (routes.length === 0) throw new Error('no routes given');
  if (tcp && allowed.length > 0) throw new Error('--tcp forwards everything; it takes no --allow patterns');
  if (!tcp && allowed.length === 0) throw new Error('no --allow patterns given (or --tcp for a preview ingress)');
  return { routes, allowed, tcp };
}

export function createTcpForwarder(route) {
  return net.createServer((client) => {
    const upstream = net.connect(route.port, route.host);
    client.pipe(upstream);
    upstream.pipe(client);
    const close = () => {
      client.destroy();
      upstream.destroy();
    };
    client.on('error', close);
    upstream.on('error', close);
    client.on('close', close);
    upstream.on('close', close);
  });
}

export function pathAllowed(rawUrl, allowed) {
  const rawPath = String(rawUrl || '').split('?', 1)[0];
  // Encoded separators and dot segments could route differently once decoded.
  if (!rawPath.startsWith('/') || /%2f|%5c|\\/i.test(rawPath)) return false;
  let path;
  try {
    path = decodeURIComponent(rawPath);
  } catch {
    return false;
  }
  if (path.split('/').some((segment) => segment === '..' || segment === '.')) return false;
  return allowed.some((pattern) => pattern.test(path));
}

function refuse(response, status, detail) {
  const body = JSON.stringify({ detail });
  response.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) });
  response.end(body);
}

export function createRelay(route, allowed) {
  const server = http.createServer((request, response) => {
    if (!pathAllowed(request.url, allowed)) {
      refuse(response, 403, 'sandbox_route_refused');
      request.resume();
      return;
    }
    const headers = { ...request.headers };
    delete headers[RELAY_HEADER];
    headers[RELAY_HEADER] = '1';
    const upstream = http.request(
      { host: route.host, port: route.port, method: request.method, path: request.url, headers },
      (reply) => {
        response.writeHead(reply.statusCode || 502, reply.rawHeaders);
        reply.pipe(response);
      },
    );
    upstream.on('error', () => {
      if (!response.headersSent) refuse(response, 502, 'sandbox_relay_upstream_failed');
      else response.destroy();
    });
    request.pipe(upstream);
  });
  // WebSocket and other upgrades are never relayed.
  server.on('upgrade', (_request, socket) => socket.destroy());
  server.on('connect', (_request, socket) => socket.destroy());
  return server;
}

function main() {
  let parsed;
  try {
    parsed = parseArguments(process.argv.slice(2));
  } catch (error) {
    console.error(String(error.message || error));
    process.exit(2);
  }
  for (const route of parsed.routes) {
    const server = parsed.tcp ? createTcpForwarder(route) : createRelay(route, parsed.allowed);
    const scope = parsed.tcp ? 'raw TCP, preview ingress' : 'agent-runtime routes only';
    server.listen(route.listen, '0.0.0.0', () => {
      console.log(`relay ${route.listen} -> ${route.host}:${route.port} (${scope})`);
    });
  }
}

// Run as a script (the relay container); importing it (tests) starts nothing.
if (process.argv[1]?.endsWith('sandbox_relay.mjs')) {
  main();
}
