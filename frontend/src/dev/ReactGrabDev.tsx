import {useEffect} from 'react';
import type {ReactGrabAPI} from 'react-grab/core';

export function ReactGrabDev() {
  useEffect(() => {
    if (!import.meta.env.DEV) return;

    let disposed = false;
    let api: ReactGrabAPI | null = null;
    (window as Window & {__REACT_GRAB_DISABLED__?: boolean}).__REACT_GRAB_DISABLED__ = true;

    void Promise.all([import('react-grab/core'), import('react-grab/styles.css')]).then(([{init}]) => {
      if (disposed) return;

      api = init({freezeReactUpdates: false});
      api.unregisterPlugin('open');
    });

    return () => {
      disposed = true;
      api?.dispose();
    };
  }, []);

  return null;
}
