import React, { useEffect, useRef, useState } from 'react';
import type { Browser as IgvBrowser, CreateOpt as IgvCreateOpt } from 'igv';
import api from '../lib/api';
import PageState from './PageState';
import { getErrorMessage } from '../lib/errorMessage';
import { loadIgv } from '../lib/igvLoader';
import { storage } from '../lib/storage';

type DestroyableIgvBrowser = IgvBrowser & {
  destroy?: () => void;
};

interface IgvViewerProps {
  familyId: string;
  sampleIds: string[];
  genome?: string;
  locus?: string;
}

interface AlignmentManifestEntry {
  sample_id: string;
  format: 'bam' | 'cram';
  url: string;
  index_url: string;
}

const IgvViewer: React.FC<IgvViewerProps> = ({ familyId, sampleIds, genome, locus }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const browserRef = useRef<DestroyableIgvBrowser | null>(null);
  const createSeqRef = useRef(0);
  const controllersRef = useRef<AbortController[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [retrySeq, setRetrySeq] = useState(0);

  const logCleanupError = (action: string, error: unknown) => {
    if (process.env.NODE_ENV !== 'production') {
      // eslint-disable-next-line no-console
      console.warn(`[IgvViewer] ${action}`, error);
    }
  };

  const abortActiveControllers = () => {
    if (!controllersRef.current.length) return;
    controllersRef.current.forEach((controller) => {
      try {
        controller.abort();
      } catch (error) {
        logCleanupError('Failed to abort request controller', error);
      }
    });
    controllersRef.current = [];
  };

  const destroyCurrentBrowser = () => {
    if (!browserRef.current) return;
    try {
      browserRef.current.destroy?.();
    } catch (error) {
      logCleanupError('Failed to destroy IGV browser instance', error);
    }
    browserRef.current = null;
  };

  const removeMountNode = (mount?: Element | null) => {
    if (!mount) return;
    const parent = containerRef.current;
    if (!parent) return;
    if (!parent.contains(mount)) return;
    try {
      parent.removeChild(mount);
    } catch (error) {
      logCleanupError('Failed to remove IGV mount node', error);
    }
  };

  const removeStaleMountNodes = (activeSeq: number | null = null) => {
    const parent = containerRef.current;
    if (!parent) return;
    try {
      const nodes = parent.querySelectorAll('[data-igv-seq]');
      nodes.forEach((node) => {
        const seqAttr = (node as HTMLElement).getAttribute('data-igv-seq');
        if (activeSeq === null || seqAttr !== String(activeSeq)) {
          removeMountNode(node);
        }
      });
    } catch (error) {
      logCleanupError('Failed to prune IGV mount nodes', error);
    }
  };

  const isAbortLikeError = (error: unknown) => {
    const candidate = error as { name?: string; code?: string } | null;
    return (
      candidate?.name === 'AbortError' ||
      candidate?.name === 'CanceledError' ||
      candidate?.code === 'ERR_CANCELED'
    );
  };

  useEffect(() => {
    const seq = ++createSeqRef.current;
    let disposed = false;
    setErrorMessage(null);

    const createBrowser = async () => {
      if (!containerRef.current) return;
      if (disposed || seq !== createSeqRef.current) return;

      // Tear down any current instance
      destroyCurrentBrowser();

      const token = storage.getItem('token');
      const base = api.defaults.baseURL;
      const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
      let mount: HTMLDivElement | null = null;

      // Reset and capture controllers for this run
      abortActiveControllers();

      const controller = new AbortController();
      controllersRef.current.push(controller);
      try {
        const manifestParams = new URLSearchParams();
        sampleIds.forEach((sampleId) => manifestParams.append('sample', sampleId));
        const manifestUrl = manifestParams.toString()
          ? `/cram/${familyId}/manifest?${manifestParams.toString()}`
          : `/cram/${familyId}/manifest`;
        const manifestResponse = await api.get(manifestUrl, { signal: controller.signal });
        const tracks = (manifestResponse.data as AlignmentManifestEntry[]).map((entry) => ({
          name: entry.sample_id,
          type: 'alignment',
          format: entry.format,
          url: `${base}${entry.url}`,
          indexURL: `${base}${entry.index_url}`,
          headers,
        }));
        type GenomeArg = string | Record<string, unknown>;
        const igv = await loadIgv();

        if (disposed || seq !== createSeqRef.current || !containerRef.current) {
          return;
        }

        // Prepare a dedicated mount node only when we are about to create the browser.
        mount = document.createElement('div');
        mount.setAttribute('data-igv-seq', String(seq));
        containerRef.current.appendChild(mount);
        const mountNode = mount;

        const createWithGenome = async (g: GenomeArg) => {
          const opts = {
            tracks,
            locus: locus,
          } as IgvCreateOpt & { reference?: unknown; genome?: string };
          if (typeof g === 'string') {
            opts.genome = g;
          } else {
            opts.reference = g;
          }
          return (await igv.createBrowser(mountNode, opts)) as IgvBrowser;
        };

        // Remap CHM13v2.0 to hs1 temporarily; default to hg38 if missing.
        const preferredGenome = (genome === 'chm13v2.0' ? 'hs1' : genome) ?? 'hg38';
        let created: DestroyableIgvBrowser;
        try {
          created = (await createWithGenome(preferredGenome)) as DestroyableIgvBrowser;
        } catch (error) {
          logCleanupError(`Failed to create IGV browser for genome ${preferredGenome}`, error);
          throw error;
        }

        // If a newer creation started or we were disposed, destroy and remove this one.
        if (seq !== createSeqRef.current || disposed) {
          try {
            created.destroy?.();
          } catch (error) {
            logCleanupError('Failed to dispose IGV browser after race', error);
          }
          removeMountNode(mount);
          return;
        }

        browserRef.current = created;

        // Ensure locus is applied even if IGV resets after track init.
        try {
          if (locus) {
            await created.search(locus);
          }
        } catch (error) {
          logCleanupError(`Failed to search locus ${locus}`, error);
        }

        // Remove any stale mount nodes from prior attempts.
        removeStaleMountNodes(seq);
      } catch (error) {
        removeMountNode(mount);
        if (disposed || seq !== createSeqRef.current || isAbortLikeError(error)) {
          return;
        }
        logCleanupError('Failed to initialize IGV browser', error);
        setErrorMessage(getErrorMessage(error, 'Unable to load the IGV viewer.'));
      } finally {
        controllersRef.current = controllersRef.current.filter(
          (activeController) => activeController !== controller,
        );
      }
    };

    const creationTimer = window.setTimeout(() => {
      void createBrowser();
    }, 0);

    return () => {
      disposed = true;
      window.clearTimeout(creationTimer);
      // Abort any in-flight probes
      abortActiveControllers();
      destroyCurrentBrowser();
      // Remove all mount nodes to ensure no lingering DOM
      removeStaleMountNodes();
    };
    // Depend on content of sampleIds to avoid identity noise
  }, [familyId, genome, locus, retrySeq, JSON.stringify(sampleIds)]);

  return (
    <>
      {errorMessage ? (
        <PageState
          kicker="Viewer"
          title="Unable to load IGV"
          message={errorMessage}
          action={
            <button
              type="button"
              onClick={() => {
                setErrorMessage(null);
                setRetrySeq((current) => current + 1);
              }}
            >
              Try again
            </button>
          }
          narrow
        />
      ) : null}
      <div
        ref={containerRef}
        className={errorMessage ? 'igv-shell hidden' : 'igv-shell'}
        aria-hidden={errorMessage ? 'true' : undefined}
      />
    </>
  );
};

export default IgvViewer;
