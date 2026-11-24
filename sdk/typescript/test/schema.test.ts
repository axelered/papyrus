import assert from 'node:assert/strict'
import { describe, test } from 'node:test'
import * as z from 'zod/mini'
import { Papyrus } from '../src/index.ts'
import * as models from '../src/models.ts'
import { loadOpenAPI, type Schema } from './openapi.ts'

// the client mirrors the API contract: a change only on the server fails here (adr/sdk-typescript.md)

const { components, paths } = loadOpenAPI()

// bounds and defaults are the server's, texts are not part of the contract
const IGNORED = new Set(['$schema', 'title', 'description', 'minimum', 'maximum', 'additionalProperties', 'default'])
// request envelopes (wire.ts builds them) and `GET /`, which the client does not call
const NOT_MIRRORED = new Set(['HelloResponse', 'OutputMode'])

const MIRRORS = new Map(
  Object.entries(models).flatMap(([name, value]) => {
    if (!(value instanceof z.ZodMiniType)) return []
    const component = name === 'fetchInfo' ? 'Fetch' : name.charAt(0).toUpperCase() + name.slice(1)
    return [[component, value]]
  }),
)

function isRecord(value: unknown): value is Schema {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNull(value: unknown): boolean {
  return isRecord(value) && value.type === 'null'
}

/** Inlined references, and nullable fields as optional ones: the server omits what it does not know */
function normalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(normalize)
  if (!isRecord(value)) return value
  if (typeof value.$ref === 'string') return normalize(component(value.$ref.split('/').at(-1) ?? ''))
  if (Array.isArray(value.anyOf) && value.anyOf.some(isNull)) {
    const [only, ...others] = value.anyOf.filter((item) => !isNull(item))
    return normalize(others.length > 0 ? { ...value, anyOf: [only, ...others] } : only)
  }
  const type = Array.isArray(value.type) ? value.type.find((item) => item !== 'null') : value.type
  const entries = Object.entries({ ...value, ...(type === undefined ? {} : { type }) })
  return Object.fromEntries(
    entries
      .filter(([key]) => !IGNORED.has(key) && key !== 'properties' && key !== 'required')
      .map(([key, item]) => [key, normalize(item)])
      .concat(
        isRecord(value.properties)
          ? [['properties', Object.fromEntries(Object.entries(value.properties).map(([k, v]) => [k, normalize(v)]))]]
          : [],
      ),
  )
}

function component(name: string): Schema {
  const schema = components.schemas[name]
  assert.ok(schema, `${name} is not in the OpenAPI document`)
  return schema
}

/** Fields always present in a response: defaults other than null are echoed */
function present(schema: Schema): string[] {
  const required = Array.isArray(schema.required) ? schema.required : []
  const properties = isRecord(schema.properties) ? schema.properties : {}
  const echoed = Object.entries(properties).flatMap(([name, property]) =>
    isRecord(property) && property.default !== undefined && property.default !== null ? [name] : [],
  )
  return [...new Set([...required, ...echoed])].sort()
}

test('every component is mirrored', () => {
  const expected = Object.keys(components.schemas).filter(
    (name) => !name.endsWith('Request') && !NOT_MIRRORED.has(name),
  )

  assert.deepEqual([...MIRRORS.keys()].sort(), expected.sort(), 'update src/models.ts')
})

describe('models match', () => {
  for (const [name, schema] of MIRRORS) {
    test(name, () => {
      const mine: Schema = z.toJSONSchema(schema, { io: 'input' })
      const theirs = structuredClone(component(name))
      if (name === 'Problem' && isRecord(theirs.properties) && isRecord(theirs.properties.code)) {
        assert.deepEqual(models.PROBLEM_CODES, theirs.properties.code.enum, 'update PROBLEM_CODES in src/models.ts')
        // open in the client, and foreign problems may have no type
        delete theirs.properties.code.enum
        delete (theirs.properties.type as Schema).default
      }

      assert.deepEqual(normalize(mine), normalize(theirs), `update ${name} in src/models.ts`)
      assert.deepEqual(present(mine), present(theirs), `update what is optional in ${name} in src/models.ts`)
    })
  }
})

type Operation = Exclude<keyof Papyrus, 'health' | 'ready'>

/** Whether the parameters take a destination, checked against the client by the compiler */
const DESTINATION: {
  [K in Operation]: 'destination' extends keyof NonNullable<Parameters<Papyrus[K]>[1]> ? true : false
} = { markdown: true, render: true, thumbnail: true, screenshot: true, inspect: false }

// what the client keeps for itself among the parameters of an operation
const CLIENT_FIELDS = ['filename', 'destination', 'timeout', 'signal']

test('every operation has a method', () => {
  const operations = Object.keys(paths).filter((path) => path.startsWith('/v1/'))
  const methods = Object.getOwnPropertyNames(Papyrus.prototype).filter((name) => name !== 'constructor')

  assert.deepEqual(
    Object.keys(DESTINATION)
      .map((name) => `/v1/${name}`)
      .sort(),
    operations.sort(),
  )
  assert.deepEqual(methods.sort(), [...Object.keys(DESTINATION), 'health', 'ready'].sort(), 'update src/client.ts')
})

describe('requests match', () => {
  for (const [name, destination] of Object.entries(DESTINATION)) {
    test(name, () => {
      const prefix = name.charAt(0).toUpperCase() + name.slice(1)
      const { properties } = component(`${prefix}Request`)
      const options = component(`${prefix}Options`).properties
      assert.ok(isRecord(properties) && isRecord(options) && isRecord(properties.options))

      assert.equal(properties.options.$ref, `#/components/schemas/${prefix}Options`)
      assert.equal('destination' in properties, destination, 'update src/client.ts')
      const envelope = new Set(['source', 'options', 'output', 'destination'])
      assert.deepEqual(
        Object.keys(properties).filter((field) => !envelope.has(field)),
        [],
        'update src/wire.ts',
      )
      assert.deepEqual(
        Object.keys(options).filter((option) => CLIENT_FIELDS.includes(option)),
        [],
      )
    })
  }
})
