import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import ClickhouseVariantOperationsSection from './ClickhouseVariantOperationsSection';
import type {
  ClickHouseVariantAssemblyList,
  StatusTone,
} from './dataManagementTypes';

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (typeof error !== 'object' || error === null) return fallback;
  const responseDetail = (
    error as { response?: { data?: { detail?: string } } }
  )?.response?.data?.detail;
  return responseDetail || (error as { message?: string }).message || fallback;
};

const AdminClickhouseManagementPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [status, setStatus] = useState<{
    tone: StatusTone;
    message: string;
  } | null>(null);

  const {
    data: variantStorage,
    isLoading,
    error,
  } = useQuery<ClickHouseVariantAssemblyList>({
    queryKey: ['admin', 'clickhouse', 'variants'],
    queryFn: async () => {
      const response = await api.get('/admin/clickhouse/variants');
      return response.data as ClickHouseVariantAssemblyList;
    },
    retry: false,
  });

  const runAction = async (
    key: string,
    confirmation: string,
    action: () => Promise<unknown>,
    successMessage: string
  ) => {
    if (!window.confirm(confirmation)) return;

    setBusyKey(key);
    setStatus(null);
    try {
      await action();
      await queryClient.invalidateQueries({
        queryKey: ['admin', 'clickhouse', 'variants'],
      });
      setStatus({ tone: 'success', message: successMessage });
    } catch (actionError) {
      setStatus({
        tone: 'error',
        message: getErrorMessage(
          actionError,
          'The ClickHouse operation failed.'
        ),
      });
    } finally {
      setBusyKey(null);
    }
  };

  if (isLoading) {
    return (
      <PageState
        kicker="Administration"
        title="Loading ClickHouse management"
        message="Preparing ClickHouse variant table status by assembly."
      />
    );
  }

  return (
    <div className="page-shell space-y-8">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">ClickHouse table management</h1>
            <p className="catalog-card-copy">
              Keep variant tables, materialized views, and mutations healthy
              outside family and sample lifecycle tasks.
            </p>
          </div>
          <div className="compact-toolbar dashboard-toolbar">
            <Link
              to="/admin/data"
              className="button-secondary hover:no-underline"
            >
              Family & sample data
            </Link>
            <Link
              to="/admin/data/presets"
              className="button-secondary hover:no-underline"
            >
              Preset filters
            </Link>
            <Link
              to="/admin/data/tags"
              className="button-secondary hover:no-underline"
            >
              Variant tags
            </Link>
            <Link
              to="/admin/data/logs"
              className="button-secondary hover:no-underline"
            >
              Audit logs
            </Link>
          </div>
        </div>
      </section>

      {status && (
        <div
          className={`status-note ${
            status.tone === 'error'
              ? 'status-note--error'
              : 'status-note--success'
          }`}
        >
          {status.message}
        </div>
      )}

      <ClickhouseVariantOperationsSection
        assemblies={variantStorage?.assemblies ?? []}
        loading={false}
        errorMessage={
          error
            ? getErrorMessage(
                error,
                'The ClickHouse variant status could not be loaded.'
              )
            : null
        }
        busyKey={busyKey}
        onRunAction={runAction}
      />
    </div>
  );
};

export default AdminClickhouseManagementPage;
