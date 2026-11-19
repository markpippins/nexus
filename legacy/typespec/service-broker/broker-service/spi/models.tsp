import "../api/models.tsp";

namespace Nexus.ServiceBroker.V1.Spi {

/**
 * Broker Service SPI Models
 * 
 * Defines the Service Provider Interface contracts for broker implementations.
 * These are the internal contracts that broker implementations must fulfill.
 */

/**
 * A parameter for a broker operation.
 */
model BrokerParam {
  /**
   * The parameter key/name.
   */
  key: string;

  /**
   * The parameter value.
   */
  value: string;
}

/**
 * A broker operation definition.
 */
model BrokerOperation {
  /**
   * The operation name.
   */
  name: string;

  /**
   * Parameters for the operation.
   */
  params: BrokerParam[];
}

}
