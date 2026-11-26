import { Papyrus, text, VERSION } from '@axelered/papyrus'

new Papyrus({ baseUrl: 'http://papyrus' })
console.log('import', VERSION, typeof text)
