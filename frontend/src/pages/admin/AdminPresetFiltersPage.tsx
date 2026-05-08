import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import SmallVariantFilterPresetTable from '../../components/SmallVariantFilterPresetTable';
import type { SmallVariantFilterPreset } from '../families/smallVariantSearch';

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (typeof error !== 'object' || error === null) return fallback;
  const responseDetail = (
    error as { response?: { data?: { detail?: string } } }
  )?.response?.data?.detail;
  return responseDetail || (error as { message?: string }).message || fallback;
};

const AdminPresetFiltersPage: React.FC = () => {
  const {
    data: savedPresets = [],
    isLoading,
    error,
  } = useQuery<SmallVariantFilterPreset[]>({
    queryKey: ['admin', 'small-variant-filter-presets'],
    queryFn: async () => {
      const response = await api.get('/admin/small-variant-filter-presets');
      return response.data as SmallVariantFilterPreset[];
    },
    retry: false,
  });

  if (isLoading) {
    return (
      <PageState
        kicker="Administration"
        title="Loading preset filters"
        message="Preparing global and family-linked small-variant filter presets."
      />
    );
  }

  if (error) {
    return (
      <PageState
        kicker="Administration"
        title="Could not load preset filters"
        message={getErrorMessage(
          error,
          'The preset filter catalog could not be loaded.'
        )}
      />
    );
  }

  return (
    <div className="page-shell space-y-8">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">Preset filter management</h1>
            <p className="catalog-card-copy">
              Audit reusable small-variant presets in one place, independent
              from family and ClickHouse operations.
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
              to="/admin/data/tags"
              className="button-secondary hover:no-underline"
            >
              Variant tags
            </Link>
            <Link
              to="/admin/data/clickhouse"
              className="button-secondary hover:no-underline"
            >
              ClickHouse tables
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

      <section className="surface-card space-y-4">
        <div className="space-y-2">
          <h2 className="section-title">Saved small-variant filters</h2>
          <p className="section-copy">
            Reusable filter presets across all users, including ownership and
            created date.
          </p>
        </div>
        <SmallVariantFilterPresetTable
          presets={savedPresets}
          emptyMessage="No saved filters available."
          showOwner
        />
      </section>
    </div>
  );
};

export default AdminPresetFiltersPage;
