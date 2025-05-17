'use client';

import { createClient } from "@/lib/supabase/client";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { motion } from "motion/react";
import { setupNewSubscription } from "@/lib/actions/billing";
import { SubmitButton } from "@/components/ui/submit-button";
import { Button } from "@/components/ui/button";
import { siteConfig } from "@/lib/home";

// Create SUBSCRIPTION_PLANS using stripePriceId from siteConfig
export const SUBSCRIPTION_PLANS = {
  FREE: siteConfig.cloudPricingItems.find(item => item.name === 'Free')?.stripePriceId || '',
  PRO: siteConfig.cloudPricingItems.find(item => item.name === 'Pro')?.stripePriceId || '',
  ENTERPRISE: siteConfig.cloudPricingItems.find(item => item.name === 'Enterprise')?.stripePriceId || '',
};

interface Plan {
  id: string;
  name: string;
  description: string;
  price: number;
  interval: string;
  features: string[];
  buttonText: string;
  buttonColor: string;
}

interface PlanComparisonProps {
  accountId: string;
  returnUrl?: string;
  isManaged?: boolean;
  onPlanSelect?: (planId: string) => void;
  className?: string;
  isCompact?: boolean;
}

export function PlanComparison({
  accountId,
  returnUrl = typeof window !== 'undefined' ? window.location.href : '',
  isManaged = true,
  onPlanSelect,
  className = "",
  isCompact = false
}: PlanComparisonProps) {
  const [currentPlanId, setCurrentPlanId] = useState<string | undefined>();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function fetchData() {
      try {
        const supabase = createClient();

        if (accountId) {
          const { data } = await supabase
            .schema('basejump')
            .from('billing_subscriptions')
            .select('price_id')
            .eq('account_id', accountId)
            .eq('status', 'active')
            .single();
          setCurrentPlanId(data?.price_id);
        }

        const { data: plansData } = await supabase.functions.invoke('billing-functions', {
          body: {
            action: "get_plans",
            args: { account_id: accountId }
          }
        });

        const transformedPlans = plansData.map((plan: any) => ({
          id: plan.id,
          name: plan.product_name || 'Unknown Plan',
          description: plan.product_description || '',
          price: (plan.price / 100).toFixed(2),
          interval: plan.interval || 'month',
          features: [],
          buttonText: plan.product_name === 'Free' ? 'Get Started' : 'Upgrade',
          buttonColor: plan.product_name === 'Free' ? 'bg-primary hover:bg-primary/90' : 'bg-primary hover:bg-primary/90'
        }));

        setPlans(transformedPlans);
      } catch (err) {
        console.error('Error in fetchData:', err);
      } finally {
        setIsLoading(false);
      }
    }

    fetchData();
  }, [accountId]);

  if (isLoading) {
    return <div>Loading plans...</div>;
  }

  return (
    <div className={cn("min-h-screen bg-gradient-to-b from-background to-background/95 p-8 space-y-8", className)}>
      <div className="text-center">
        <h2 className="text-4xl font-bold bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent mb-4">
          Choose Your Plan
        </h2>
        <p className="text-base text-muted-foreground max-w-lg mx-auto">
          Select a plan that suits your needs. Enjoy flexible pricing with powerful features.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 w-full gap-6">
        {plans.map((plan) => (
          <div 
            key={plan.id} 
            className="border w-full border-border rounded-[1em] p-8 shadow-lg hover:shadow-xl transition-all duration-300 hover:scale-105 hover:border-primary/50"
          >
            <h3 className="text-3xl font-semibold mb-3">{plan.name}</h3>
            <p className="text-base mb-6 text-muted-foreground">{plan.description}</p>
            <div className="text-2xl font-bold mb-6">${plan.price}/{plan.interval}</div>
            <ul className="space-y-3 mb-8">
              {plan.features.map((feature, index) => (
                <li key={index} className="text-base text-muted-foreground">- {feature}</li>
              ))}
            </ul>
            <Button
              className={cn(
                "w-full py-3 rounded-[2em] cursor-pointer font-medium text-lg transition-all duration-300",
                plan.buttonColor,
                "hover:scale-105 hover:shadow-lg"
              )}
              onClick={() => onPlanSelect?.(plan.id)}
            >
              {plan.buttonText}
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}
