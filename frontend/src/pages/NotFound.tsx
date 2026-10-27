import React from 'react';
import { Link, useLocation } from 'react-router';
import PageState from '../components/PageState';

const NotFound: React.FC = () => {
  const location = useLocation();
  return (
    <PageState
      kicker="Not found"
      title="Page not found"
      message={`No route matches ${location.pathname}.`}
      narrow
      action={
        <>
          <Link to="/dashboard" className="form-button hover:no-underline">
            Go to dashboard
          </Link>
          <button onClick={() => window.history.back()} className="button-ghost">
            Go back
          </button>
        </>
      }
    />
  );
};

export default NotFound;
