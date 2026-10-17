/**
 * Generates the gateway types per feature (PA-2.4) from `src/api/generated/openapi.json`
 * and `route-owners.json`:
 *
 * - `src/api/generated/core.ts`: kernel operations, and every schema that more than
 *   one web feature (or the kernel) reaches;
 * - `src/features/<name>/api/generated.ts`: the operations of the backend modules in
 *   that feature's `backendModules`, the schemas only it reaches, and every
 *   `core.ts` schema, re-exported. Feature code reads schemas only from its own file.
 *
 * So a schema that a second feature starts using moves into `core.ts` and only
 * generated files change. Usage: node scripts/generate-api-types.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import openapiTS, { astToString } from 'openapi-typescript';
import ts from 'typescript';

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const generatedDir = path.join(webRoot, 'src', 'api', 'generated');
const featureRoot = path.join(webRoot, 'src', 'features');
const CORE = 'core';
const KERNEL_OWNER = 'kernel';
const SCHEMA_REF = /^#\/components\/schemas\/([^/]+)$/;

const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));
const document = readJson(path.join(generatedDir, 'openapi.json'));
const owners = readJson(path.join(generatedDir, 'route-owners.json')).operations;
const schemas = document.components?.schemas ?? {};

function fail(message) {
  throw new Error(`generate-api-types: ${message}`);
}

/**
 * Every feature with a manifest; backend module id -> its feature, from the literal
 * `backendModules` lists; and feature -> the other modules' operations it lists in `usesOperations`.
 */
function manifestFeatures() {
  const result = new Map();
  const featureNames = [];
  const usedOperations = new Map();
  for (const entry of fs.readdirSync(featureRoot, { withFileTypes: true })) {
    const manifest = path.join(featureRoot, entry.name, 'module.ts');
    if (!entry.isDirectory() || !fs.existsSync(manifest)) continue;
    featureNames.push(entry.name);
    // A module.ts may hold several manifests (platform); their lists combine.
    const source = fs.readFileSync(manifest, 'utf8');
    const lists = [...source.matchAll(/\bbackendModules:\s*\[([^\]]*)\]/g)];
    if (!lists.length) fail(`${entry.name}/module.ts declares no literal backendModules list`);
    const modules = lists.flatMap((match) => match[1].split(',')).map((item) => item.trim()).filter(Boolean);
    for (const literal of modules) {
      const module = /^'([a-z0-9-]+)'$/.exec(literal)?.[1];
      if (!module) fail(`${entry.name}/module.ts: backendModules entry ${literal} is not a string literal`);
      if (result.has(module)) fail(`backend module ${module} is claimed by ${result.get(module)} and ${entry.name}`);
      result.set(module, entry.name);
    }
    const operations = [...source.matchAll(/\busesOperations:\s*\[([^\]]*)\]/g)]
      .flatMap((match) => match[1].split(',')).map((item) => item.trim()).filter(Boolean);
    for (const literal of operations) {
      const operation = /^'([A-Z]+ \/[^']*)'$/.exec(literal)?.[1];
      if (!operation) fail(`${entry.name}/module.ts: usesOperations entry ${literal} is not a 'METHOD /path' literal`);
      usedOperations.set(entry.name, [...(usedOperations.get(entry.name) ?? []), operation]);
    }
  }
  return { featureNames: featureNames.sort(), features: result, usedOperations };
}

function schemaRefs(value, found = new Set()) {
  if (Array.isArray(value)) {
    for (const item of value) schemaRefs(item, found);
  } else if (value && typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) {
      const name = key === '$ref' && typeof item === 'string' ? SCHEMA_REF.exec(item)?.[1] : undefined;
      if (name) found.add(name);
      else schemaRefs(item, found);
    }
  }
  return found;
}

function closure(seeds) {
  const reached = new Set();
  const pending = [...seeds];
  while (pending.length) {
    const name = pending.pop();
    if (reached.has(name)) continue;
    if (!(name in schemas)) fail(`unresolved schema reference ${name}`);
    reached.add(name);
    pending.push(...schemaRefs(schemas[name]));
  }
  return reached;
}

// Group operations: kernel operations into core, the rest into the feature that claims their owner.
const { featureNames, features, usedOperations } = manifestFeatures();
const groups = new Map([[CORE, {}]]);
for (const [route, item] of Object.entries(document.paths ?? {})) {
  for (const [method, operation] of Object.entries(item)) {
    const owner = owners[`${method.toUpperCase()} ${route}`];
    if (!owner) fail(`${method.toUpperCase()} ${route} has no owner; run api:schema`);
    const group = owner === KERNEL_OWNER ? CORE : features.get(owner);
    if (!group) fail(`backend module ${owner} owns ${method.toUpperCase()} ${route} but no web feature lists it in backendModules`);
    const paths = groups.get(group) ?? {};
    paths[route] = { ...paths[route], [method]: operation };
    groups.set(group, paths);
  }
}
// Another module's operations a feature also calls join its paths; their schemas become shared.
for (const [feature, operations] of usedOperations) {
  for (const key of operations) {
    const owner = owners[key];
    if (!owner) fail(`${feature} uses ${key}, which the gateway does not document`);
    if (owner === KERNEL_OWNER || features.get(owner) === feature) fail(`${feature} lists its own or a kernel operation in usesOperations: ${key}`);
    const [method, route] = key.split(' ');
    const paths = groups.get(feature) ?? {};
    paths[route] = { ...paths[route], [method.toLowerCase()]: document.paths[route][method.toLowerCase()] };
    groups.set(feature, paths);
  }
}

// A schema reached by exactly one feature is defined there; everything else is core,
// and so is everything a core schema references.
const reachedBy = new Map();
const reach = new Map();
for (const [group, paths] of groups) {
  const reached = closure(schemaRefs(paths));
  reach.set(group, reached);
  for (const name of reached) reachedBy.set(name, new Set([...(reachedBy.get(name) ?? []), group]));
}
const coreSchemas = closure(Object.keys(schemas).filter((name) => {
  const groupsReaching = reachedBy.get(name);
  return !groupsReaching || groupsReaching.size !== 1 || groupsReaching.has(CORE);
}));

const literal = (text) => ts.factory.createLiteralTypeNode(ts.factory.createStringLiteral(text));
const coreSchemaType = (name) => ts.factory.createIndexedAccessTypeNode(
  ts.factory.createIndexedAccessTypeNode(ts.factory.createTypeReferenceNode('core'), literal('schemas')),
  literal(name),
);

async function render(paths, names, { reexportCore }) {
  const subset = {
    openapi: document.openapi,
    info: document.info,
    paths,
    components: { schemas: Object.fromEntries([...names].sort().map((name) => [name, schemas[name]])) },
  };
  const ast = await openapiTS(subset, {
    defaultNonNullable: false,
    transform(schemaObject, options) {
      const name = SCHEMA_REF.exec(options.path ?? '')?.[1];
      return reexportCore && name && coreSchemas.has(name) ? coreSchemaType(name) : undefined;
    },
  });
  const body = astToString(ast);
  const header = '/**\n * Generated by scripts/generate-api-types.mjs from openapi.json and route-owners.json; do not edit.\n */\n';
  const imports = reexportCore && [...names].some((name) => coreSchemas.has(name))
    ? "import type { components as core } from '../../../api/generated/core';\n\n"
    : '';
  return `${header}${imports}${body}`;
}

const outputs = new Map();
outputs.set(path.join(generatedDir, 'core.ts'), await render(groups.get(CORE), coreSchemas, { reexportCore: false }));
for (const feature of featureNames) {
  const target = path.join(featureRoot, feature, 'api', 'generated.ts');
  // Every core schema is re-exported too, so feature code reads kernel schemas from here as well.
  const names = new Set([...(reach.get(feature) ?? []), ...coreSchemas]);
  outputs.set(target, await render(groups.get(feature) ?? {}, names, { reexportCore: true }));
}
for (const [target, text] of outputs) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, text);
}
console.log(`generate-api-types: core.ts (${coreSchemas.size} schemas) and ${outputs.size - 1} feature files`);
