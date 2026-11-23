/* Lets `node --experimental-strip-types` load the app's TypeScript modules, whose relative
 * imports omit the `.ts` extension (Vite resolves them; plain node does not).
 *
 *   node --experimental-strip-types --import ./scripts/ts-resolve.mjs scripts/<test>.mjs
 */
import {register} from 'node:module';

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(specifier, context, next) {
  try {
    return await next(specifier, context);
  } catch (error) {
    if (error?.code !== 'ERR_MODULE_NOT_FOUND' || !/^\\.{1,2}\\//.test(specifier) || /\\.[cm]?[jt]s$/.test(specifier)) throw error;
    return next(specifier + '.ts', context);
  }
}
`));
