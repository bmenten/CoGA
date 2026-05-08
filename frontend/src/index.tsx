import React, { Suspense, lazy } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/Layout';
import RequireAuth from './components/RequireAuth';
import RequireAdmin from './components/RequireAdmin';
import SessionRedirect from './components/SessionRedirect';
import { createAppQueryClient } from './lib/queryClient';
import './styles/theme.css';
import './index.css';

const queryClient = createAppQueryClient();

const LoginPage = lazy(() => import('./pages/auth/LoginPage'));
const SignupPage = lazy(() => import('./pages/auth/SignupPage'));
const Dashboard = lazy(() => import('./pages/dashboard/Dashboard'));
const FamilyIntakePage = lazy(
  () => import('./pages/dashboard/FamilyIntakePage')
);
const FamiliesPage = lazy(() => import('./pages/families/FamiliesPage'));
const FamilyDetailPage = lazy(
  () => import('./pages/families/FamilyDetailPage')
);
const GenomeOverviewPage = lazy(
  () => import('./pages/genome/GenomeOverviewPage')
);
const ChromosomeViewPage = lazy(
  () => import('./pages/genome/ChromosomeViewPage')
);
const CircosPlotPage = lazy(() => import('./pages/genome/CircosPlotPage'));
const CnvDetailsPage = lazy(() => import('./pages/genome/CnvDetailsPage'));
const PedUpload = lazy(() => import('./pages/uploads/PedUpload'));
const SampleUpload = lazy(() => import('./pages/uploads/SampleUpload'));
const FamilyStructuralVariantsPage = lazy(
  () => import('./pages/families/FamilyStructuralVariantsPage')
);
const FamilyVariantSummaryPage = lazy(
  () => import('./pages/families/FamilyVariantSummaryPage')
);
const FamilySmallVariantsPage = lazy(
  () => import('./pages/families/FamilySmallVariantsPage')
);
const FamilyRepeatExpansionsPage = lazy(
  () => import('./pages/families/FamilyRepeatExpansionsPage')
);
const FamilyParaphasePage = lazy(
  () => import('./pages/families/FamilyParaphasePage')
);
const GeneInfoPage = lazy(() => import('./pages/genes/GeneInfoPage'));
const HpoTermsPage = lazy(() => import('./pages/phenotypes/HpoTermsPage'));
const GenePanelsPage = lazy(() => import('./pages/panels/GenePanelsPage'));
const GenePanelDetailPage = lazy(
  () => import('./pages/panels/GenePanelDetailPage')
);
const UserListPage = lazy(() => import('./pages/admin/UserListPage'));
const DataManagementPage = lazy(
  () => import('./pages/admin/DataManagementPage')
);
const AdminClickhouseManagementPage = lazy(
  () => import('./pages/admin/AdminClickhouseManagementPage')
);
const AdminPresetFiltersPage = lazy(
  () => import('./pages/admin/AdminPresetFiltersPage')
);
const AdminVariantTagsPage = lazy(
  () => import('./pages/admin/AdminVariantTagsPage')
);
const AdminAuditLogsPage = lazy(
  () => import('./pages/admin/AdminAuditLogsPage')
);
const GeneReferenceAdminPage = lazy(
  () => import('./pages/admin/GeneReferenceAdminPage')
);
const ProjectsPage = lazy(() => import('./pages/projects/ProjectsPage'));
const ReferenceCatalogPage = lazy(
  () => import('./pages/reference/ReferenceCatalogPage')
);
const SettingsPage = lazy(() => import('./pages/settings/SettingsPage'));
const UserGuidePage = lazy(() => import('./pages/docs/UserGuidePage'));
const NewFeaturesPage = lazy(() => import('./pages/product/NewFeaturesPage'));
const FamilyIgvPage = lazy(() => import('./pages/families/FamilyIgvPage'));
const NotFound = lazy(() => import('./pages/NotFound'));

const RouteFallback: React.FC = () => (
  <div className="page-shell py-24 text-center text-sm text-[var(--color-text-muted)]">
    Loading...
  </div>
);

const routeElement = (element: React.ReactElement) => (
  <Suspense fallback={<RouteFallback />}>{element}</Suspense>
);

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route
              path="/"
              element={
                <SessionRedirect
                  authenticatedTo="/dashboard"
                  unauthenticatedTo="/login"
                />
              }
            />
            <Route path="/login" element={routeElement(<LoginPage />)} />
            <Route path="/signup" element={routeElement(<SignupPage />)} />
            <Route element={<RequireAuth />}>
              <Route path="/dashboard" element={routeElement(<Dashboard />)} />
              <Route
                path="/family-intake"
                element={routeElement(<FamilyIntakePage />)}
              />
              <Route
                path="/families"
                element={routeElement(<FamiliesPage />)}
              />
              <Route
                path="/families/:familyId"
                element={routeElement(<FamilyDetailPage />)}
              />
              <Route
                path="/families/:familyId/genome"
                element={routeElement(<GenomeOverviewPage />)}
              />
              <Route
                path="/families/:familyId/chromosome/:chrom"
                element={routeElement(<ChromosomeViewPage />)}
              />
              <Route
                path="/families/:familyId/circos"
                element={routeElement(<CircosPlotPage />)}
              />
              <Route
                path="/families/:familyId/structural-variants"
                element={routeElement(<FamilyStructuralVariantsPage />)}
              />
              <Route
                path="/families/:familyId/small-variants"
                element={routeElement(<FamilySmallVariantsPage />)}
              />
              <Route
                path="/families/:familyId/variant-summary"
                element={routeElement(<FamilyVariantSummaryPage />)}
              />
              <Route
                path="/families/:familyId/repeat-expansions"
                element={routeElement(<FamilyRepeatExpansionsPage />)}
              />
              <Route
                path="/families/:familyId/paraphase"
                element={routeElement(<FamilyParaphasePage />)}
              />
              <Route
                path="/families/:familyId/igv"
                element={routeElement(<FamilyIgvPage />)}
              />
              <Route path="/genes" element={routeElement(<GeneInfoPage />)} />
              <Route path="/hpo" element={routeElement(<HpoTermsPage />)} />
              <Route
                path="/settings"
                element={routeElement(<SettingsPage />)}
              />
              <Route
                path="/new-features"
                element={routeElement(<NewFeaturesPage />)}
              />
              <Route
                path="/reference-data"
                element={routeElement(<ReferenceCatalogPage />)}
              />
              <Route
                path="/cnv-details"
                element={routeElement(<CnvDetailsPage />)}
              />
              <Route
                path="/panels"
                element={routeElement(<GenePanelsPage />)}
              />
              <Route
                path="/panels/:panelId"
                element={routeElement(<GenePanelDetailPage />)}
              />
              <Route path="/docs" element={routeElement(<UserGuidePage />)} />
              <Route element={<RequireAdmin />}>
                <Route
                  path="/admin/users"
                  element={routeElement(<UserListPage />)}
                />
                <Route
                  path="/admin/data"
                  element={routeElement(<DataManagementPage />)}
                />
                <Route
                  path="/admin/data/clickhouse"
                  element={routeElement(<AdminClickhouseManagementPage />)}
                />
                <Route
                  path="/admin/data/presets"
                  element={routeElement(<AdminPresetFiltersPage />)}
                />
                <Route
                  path="/admin/data/tags"
                  element={routeElement(<AdminVariantTagsPage />)}
                />
                <Route
                  path="/admin/data/logs"
                  element={routeElement(<AdminAuditLogsPage />)}
                />
                <Route
                  path="/admin/gene-reference"
                  element={routeElement(<GeneReferenceAdminPage />)}
                />
                <Route
                  path="/projects"
                  element={routeElement(<ProjectsPage />)}
                />
                <Route path="/upload" element={routeElement(<PedUpload />)} />
                <Route
                  path="/upload-data"
                  element={routeElement(<SampleUpload />)}
                />
              </Route>
              <Route path="*" element={routeElement(<NotFound />)} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
