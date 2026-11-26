const { Papyrus, text, VERSION } = require('@axelered/papyrus')

new Papyrus({ baseUrl: 'http://papyrus' })
console.log('require', VERSION, typeof text)
